"""Incremental, idempotent indexing of Phase 3 active chunk generations into Qdrant."""

import logging
import os
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any

from qdrant_client import QdrantClient, models
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from rfp.core.config import Settings
from rfp.core.logging import log_event
from rfp.db.models import ChunkContent, ChunkGeneration, DocumentChunk, Status
from rfp.schemas.chunks import ChunkingResult, ChunkOccurrence, sha256
from rfp.schemas.parsed import ParseStatus
from rfp.search.cache import CachedEmbedding, EmbeddingCache
from rfp.search.collection import DENSE_VECTOR, SPARSE_VECTOR, CollectionManager, qdrant_call
from rfp.search.errors import IndexingFailed, QdrantUnavailable, SearchServiceError
from rfp.search.models import ModelRuntime, validate_dense, validate_sparse
from rfp.search.payload import build_payload, point_id

logger = logging.getLogger("rfp.search.indexing")


@dataclass
class GenerationInput:
    generation_id: uuid.UUID
    bid_id: uuid.UUID
    document_id: uuid.UUID
    relative_path: str
    status: str
    occurrences: list[ChunkOccurrence]


@dataclass
class GenerationOutcome:
    generation_id: uuid.UUID
    bid_id: uuid.UUID
    document_id: uuid.UUID
    relative_path: str
    status: str = "pending"
    point_count: int = 0
    unique_contents: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    dense_computed: int = 0
    sparse_computed: int = 0
    truncated_inputs: int = 0
    superseded_points_deactivated: int = 0
    # Counted from Qdrant after the delete, not assumed from the request.
    superseded_points_deleted: int = 0
    # Highest attempt number any upsert batch needed (1 = no retry), not the number of batches.
    attempts: int = 0
    upsert_batches: int = 0
    upsert_retries: int = 0
    error_code: str | None = None
    timings_ms: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "generation_id": str(self.generation_id),
            "bid_id": str(self.bid_id),
            "document_id": str(self.document_id),
            "relative_path": self.relative_path,
            "status": self.status,
            "point_count": self.point_count,
            "unique_contents": self.unique_contents,
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "dense_computed": self.dense_computed,
            "sparse_computed": self.sparse_computed,
            "truncated_inputs": self.truncated_inputs,
            "superseded_points_deactivated": self.superseded_points_deactivated,
            "superseded_points_deleted": self.superseded_points_deleted,
            "attempts": self.attempts,
            "upsert_batches": self.upsert_batches,
            "upsert_retries": self.upsert_retries,
            "error_code": self.error_code,
            "timings_ms": self.timings_ms,
        }


@dataclass
class IndexReport:
    run_id: str
    collection: str
    profile_fingerprint: str
    outcomes: list[GenerationOutcome] = field(default_factory=list)
    continuation_required: bool = False
    input_failures: list[dict[str, str]] = field(default_factory=list)
    total_ms: float = 0.0

    @property
    def succeeded(self) -> bool:
        return (
            bool(self.outcomes)
            and all(o.status in {"completed", "unchanged"} for o in self.outcomes)
            and not self.input_failures
        )

    def as_dict(self) -> dict[str, Any]:
        def total(name: str) -> int:
            return sum(getattr(outcome, name) for outcome in self.outcomes)

        return {
            "run_id": self.run_id,
            "collection": self.collection,
            "embedding_profile": self.profile_fingerprint,
            "generations": len(self.outcomes),
            "completed": sum(o.status == "completed" for o in self.outcomes),
            "unchanged": sum(o.status == "unchanged" for o in self.outcomes),
            "failed": sum(o.status == "failed" for o in self.outcomes),
            "pending": sum(o.status == "pending" for o in self.outcomes),
            "points": total("point_count"),
            "unique_contents": total("unique_contents"),
            "cache_hits": total("cache_hits"),
            "cache_misses": total("cache_misses"),
            "dense_computed": total("dense_computed"),
            "sparse_computed": total("sparse_computed"),
            "truncated_inputs": total("truncated_inputs"),
            "upsert_retries": total("upsert_retries"),
            "continuation_required": self.continuation_required,
            "input_failures": self.input_failures,
            "total_ms": round(self.total_ms, 1),
            "outcomes": [outcome.as_dict() for outcome in self.outcomes],
        }


def _match(key: str, value: Any) -> Any:
    return models.FieldCondition(key=key, match=models.MatchValue(value=value))


def validate_generation(generation: GenerationInput) -> None:
    if not generation.occurrences:
        raise IndexingFailed("Generation has no source occurrences", "empty_chunk_generation")
    for occurrence in generation.occurrences:
        if (
            occurrence.generation_id != generation.generation_id
            or occurrence.bid_id != generation.bid_id
            or occurrence.document_id != generation.document_id
            or occurrence.relative_path != generation.relative_path
            or not occurrence.normalized_text.strip()
            or sha256(occurrence.normalized_text.encode("utf-8")) != occurrence.content_hash
            or not occurrence.source_segments
        ):
            raise IndexingFailed("Chunk provenance failed validation", "invalid_chunk_input")


class IndexingService:
    def __init__(
        self,
        runtime: ModelRuntime,
        client: QdrantClient,
        cache: EmbeddingCache,
        settings: Settings,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.runtime = runtime
        self.client = client
        self.cache = cache
        self.settings = settings
        self.config = settings.search
        self.sleep = sleep

    def collection(self) -> CollectionManager:
        return CollectionManager(
            self.client, self.settings.qdrant_collection, self.runtime.profile()
        )

    def index(
        self,
        generations: list[GenerationInput],
        run_id: str | None = None,
        deadline: float | None = None,
    ) -> IndexReport:
        started = time.monotonic()
        manager = self.collection()
        manager.ensure()
        profile = self.runtime.profile()
        report = IndexReport(
            run_id=run_id or uuid.uuid4().hex,
            collection=manager.name,
            profile_fingerprint=profile.fingerprint,
        )
        ordered = sorted(generations, key=lambda g: (str(g.bid_id), g.relative_path))
        report.outcomes = [
            GenerationOutcome(g.generation_id, g.bid_id, g.document_id, g.relative_path)
            for g in ordered
        ]
        for generation, outcome in zip(ordered, report.outcomes, strict=True):
            if deadline is not None and time.monotonic() > deadline:
                report.continuation_required = True
                continue
            try:
                self._index_generation(manager, generation, outcome, report.run_id)
            except SearchServiceError as exc:
                outcome.status = "failed"
                outcome.error_code = exc.code
                self._discard_staging(manager, generation)
                log_event(
                    logger,
                    "index_generation_failed",
                    logging.ERROR,
                    generation_id=str(generation.generation_id),
                    code=exc.code,
                    attempts=outcome.attempts,
                )
                if isinstance(exc, QdrantUnavailable) and (
                    exc.retryable or exc.code == "vector_store_unauthorized"
                ):
                    # Remaining generations would fail the same way; keep them pending.
                    break
        report.total_ms = (time.monotonic() - started) * 1000
        log_event(
            logger,
            "index_run_completed",
            run_id=report.run_id,
            collection=report.collection,
            profile=profile.short,
            counts={k: v for k, v in report.as_dict().items() if isinstance(v, int | float)},
        )
        return report

    def _count(self, manager: CollectionManager, conditions: list, must_not=None) -> int:
        return qdrant_call(
            lambda: (
                self.client.count(
                    manager.name,
                    count_filter=models.Filter(must=conditions, must_not=must_not),
                    exact=True,
                ).count
            )
        )

    def _index_generation(
        self,
        manager: CollectionManager,
        generation: GenerationInput,
        outcome: GenerationOutcome,
        run_id: str,
    ) -> None:
        validate_generation(generation)
        profile = self.runtime.profile()
        generation_filter = [
            _match("chunk_generation_id", str(generation.generation_id)),
            _match("profile_fingerprint", profile.fingerprint),
        ]
        expected = len(generation.occurrences)
        outcome.unique_contents = len({o.content_hash for o in generation.occurrences})
        active_count = self._count(manager, [*generation_filter, _match("index_active", True)])
        total_count = self._count(manager, generation_filter)
        if active_count == expected and total_count == expected:
            outcome.status = "unchanged"
            outcome.point_count = expected
            self._supersede(manager, generation, outcome)
            return

        timer = time.monotonic()
        texts = {o.content_hash: o.normalized_text for o in generation.occurrences}
        cached = self.cache.load(profile, list(texts))
        missing = [h for h in texts if h not in cached]
        outcome.cache_hits = len(texts) - len(missing)
        outcome.cache_misses = len(missing)
        outcome.timings_ms["cache_load"] = (time.monotonic() - timer) * 1000

        timer = time.monotonic()
        computed = self._embed(missing, texts)
        outcome.dense_computed = outcome.sparse_computed = len(computed)
        outcome.timings_ms["embedding"] = (time.monotonic() - timer) * 1000
        timer = time.monotonic()
        self.cache.store(profile, computed)
        outcome.timings_ms["cache_store"] = (time.monotonic() - timer) * 1000
        cached.update({entry.content_hash: entry for entry in computed})

        points = []
        for occurrence in generation.occurrences:
            vectors = cached[occurrence.content_hash]
            outcome.truncated_inputs += int(vectors.truncated)
            points.append(
                models.PointStruct(
                    id=point_id(occurrence.occurrence_id, occurrence.generation_id),
                    vector={
                        DENSE_VECTOR: list(vectors.dense),
                        SPARSE_VECTOR: models.SparseVector(
                            indices=list(vectors.sparse.indices),
                            values=list(vectors.sparse.values),
                        ),
                    },
                    payload=build_payload(
                        occurrence,
                        generation_status=generation.status,
                        profile_fingerprint=profile.fingerprint,
                        indexing_run_id=run_id,
                        token_count=vectors.token_count,
                        truncated=vectors.truncated,
                    ),
                )
            )
        timer = time.monotonic()
        for start in range(0, len(points), self.config.upsert_batch_size):
            self._upsert(manager, points[start : start + self.config.upsert_batch_size], outcome)
        outcome.timings_ms["upsert"] = (time.monotonic() - timer) * 1000

        # Verify before any visibility change: exact count, matching profile, no stray points.
        if self._count(manager, generation_filter) != expected or self._count(
            manager,
            [_match("chunk_generation_id", str(generation.generation_id))],
            must_not=[_match("profile_fingerprint", profile.fingerprint)],
        ):
            raise IndexingFailed("Indexed point verification failed", "index_verification_failed")
        qdrant_call(
            lambda: self.client.set_payload(
                manager.name,
                payload={"index_active": True},
                points=models.Filter(must=generation_filter),
                wait=True,
            )
        )
        if self._count(manager, [*generation_filter, _match("index_active", True)]) != expected:
            raise IndexingFailed("Activation verification failed", "index_activation_failed")
        self._supersede(manager, generation, outcome)
        outcome.point_count = expected
        outcome.status = "completed"
        log_event(
            logger,
            "index_generation_completed",
            generation_id=str(generation.generation_id),
            points=expected,
            unique_contents=outcome.unique_contents,
            cache_hits=outcome.cache_hits,
            cache_misses=outcome.cache_misses,
            truncated_inputs=outcome.truncated_inputs,
            attempts=outcome.attempts,
            upsert_batches=outcome.upsert_batches,
            upsert_retries=outcome.upsert_retries,
            timings_ms={k: round(v, 1) for k, v in outcome.timings_ms.items()},
        )

    def _embed(self, hashes: list[str], texts: dict[str, str]) -> list[CachedEmbedding]:
        result: list[CachedEmbedding] = []
        dense_model = self.runtime.dense
        sparse_model = self.runtime.sparse
        for start in range(0, len(hashes), self.config.embed_batch_size):
            batch = hashes[start : start + self.config.embed_batch_size]
            batch_texts = [texts[h] for h in batch]
            dense = self.runtime.infer(
                partial(dense_model.embed_documents, batch_texts), "dense_embedding_failed"
            )
            counts = self.runtime.infer(
                partial(dense_model.token_counts, batch_texts), "dense_embedding_failed"
            )
            sparse = self.runtime.infer(
                partial(sparse_model.embed_documents, batch_texts), "sparse_embedding_failed"
            )
            if not len(dense) == len(sparse) == len(counts) == len(batch):
                raise IndexingFailed("Embedding batch size mismatch", "embedding_count_mismatch")
            for content_hash, vector, sparse_vector, count in zip(
                batch, dense, sparse, counts, strict=True
            ):
                checked = validate_sparse(sparse_vector.indices, sparse_vector.values)
                result.append(
                    CachedEmbedding(
                        content_hash=content_hash,
                        dense=tuple(validate_dense(vector, dense_model.dim)),
                        sparse=checked,
                        token_count=int(count),
                        truncated=int(count) > dense_model.max_tokens,
                    )
                )
        return result

    def _upsert(self, manager: CollectionManager, points: list, outcome: GenerationOutcome) -> None:
        outcome.upsert_batches += 1
        for attempt in range(self.config.upsert_attempts):
            outcome.attempts = max(outcome.attempts, attempt + 1)
            outcome.upsert_retries += int(attempt > 0)
            try:
                qdrant_call(lambda: self.client.upsert(manager.name, points=points, wait=True))
                return
            except QdrantUnavailable as exc:
                log_event(
                    logger,
                    "qdrant_upsert_retry",
                    logging.WARNING,
                    attempt=attempt + 1,
                    code=exc.code,
                )
                if not exc.retryable or attempt + 1 == self.config.upsert_attempts:
                    raise
                self.sleep(min(2**attempt, 8))

    def _supersede(
        self, manager: CollectionManager, generation: GenerationInput, outcome: GenerationOutcome
    ) -> None:
        """Retire other generations for the same source only after replacement verification."""
        source = [
            _match("bid_id", str(generation.bid_id)),
            _match("relative_path", generation.relative_path),
        ]
        others = [_match("chunk_generation_id", str(generation.generation_id))]
        active = self._count(manager, [*source, _match("index_active", True)], must_not=others)
        if active:
            qdrant_call(
                lambda: self.client.set_payload(
                    manager.name,
                    payload={"index_active": False},
                    points=models.Filter(must=source, must_not=others),
                    wait=True,
                )
            )
        outcome.superseded_points_deactivated = active
        stale = self._count(manager, source, must_not=others)
        if not stale:
            return
        if not self.config.delete_superseded_points:
            log_event(
                logger,
                "superseded_points_retained",
                generation_id=str(generation.generation_id),
                points=stale,
            )
            return
        qdrant_call(
            lambda: self.client.delete(
                manager.name,
                points_selector=models.FilterSelector(
                    filter=models.Filter(must=source, must_not=others)
                ),
                wait=True,
            )
        )
        remaining = self._count(manager, source, must_not=others)
        outcome.superseded_points_deleted = stale - remaining
        if remaining:
            raise IndexingFailed(
                "Superseded points remain after deletion", "superseded_delete_unverified"
            )

    def _discard_staging(self, manager: CollectionManager, generation: GenerationInput) -> None:
        # Invisible staging points only; previously active evidence is never touched here.
        try:
            qdrant_call(
                lambda: self.client.delete(
                    manager.name,
                    points_selector=models.FilterSelector(
                        filter=models.Filter(
                            must=[
                                _match("chunk_generation_id", str(generation.generation_id)),
                                _match("index_active", False),
                            ]
                        )
                    ),
                    wait=True,
                )
            )
        except SearchServiceError:
            log_event(logger, "index_staging_cleanup_failed", logging.WARNING)


def load_local_artifacts(
    path: Path, settings: Settings
) -> tuple[list[GenerationInput], list[dict[str, str]]]:
    """Read Phase 3 ``ChunkingResult`` JSON files and pick one generation per bid/source path."""
    root = path.expanduser().resolve()
    if not root.is_dir():
        raise IndexingFailed("Chunk artifact directory not found", "input_directory_not_found")
    failures: list[dict[str, str]] = []
    candidates: dict[tuple[uuid.UUID, str], list[ChunkingResult]] = {}
    files = 0
    total_bytes = 0
    for base, directories, names in os.walk(root, followlinks=False):
        directories[:] = sorted(
            d for d in directories if not d.startswith(".") and not (Path(base) / d).is_symlink()
        )
        for name in sorted(names):
            source = Path(base) / name
            if (
                source.is_symlink()
                or not name.endswith(".json")
                or name == "summary.json"
                or name.startswith(".")
            ):
                continue
            relative = source.relative_to(root).as_posix()
            files += 1
            if files > settings.ingestion_max_files:
                raise IndexingFailed("Too many chunk artifacts", "input_file_count_limit")
            try:
                size = source.stat().st_size
                total_bytes += size
                if (
                    size > settings.chunking.max_artifact_bytes
                    or total_bytes > settings.ingestion_max_total_bytes
                ):
                    raise ValueError
                result = ChunkingResult.model_validate_json(source.read_bytes())
            except Exception:
                # Messages may contain document text; expose only path and code.
                failures.append({"path": relative, "code": "invalid_chunk_artifact"})
                continue
            if result.skipped or result.processing_status == ParseStatus.failed:
                failures.append({"path": relative, "code": "chunk_generation_not_indexable"})
                continue
            candidates.setdefault((result.bid_id, result.relative_path), []).append(result)
    generations = []
    current = settings.chunking.fingerprint
    for (bid_id, relative_path), results in sorted(candidates.items(), key=lambda i: str(i[0])):
        unique = {result.generation_id: result for result in results}
        chosen = list(unique.values())
        if len(chosen) > 1:
            chosen = [r for r in chosen if r.configuration_hash == current]
        if len(chosen) != 1:
            failures.append({"path": relative_path, "code": "ambiguous_local_generation"})
            continue
        result = chosen[0]
        generations.append(
            GenerationInput(
                generation_id=result.generation_id,
                bid_id=bid_id,
                document_id=result.document_id,
                relative_path=relative_path,
                status=(
                    "completed"
                    if result.processing_status == ParseStatus.succeeded
                    else "partially_completed"
                ),
                occurrences=result.occurrences,
            )
        )
    return generations, failures


def load_active_generations(
    engine: Engine, bid_id: uuid.UUID, generation_ids: list[uuid.UUID] | None = None
) -> tuple[list[GenerationInput], list[dict[str, str]]]:
    """Active Phase 3 generations joined to occurrences and canonical text (short session)."""
    failures: list[dict[str, str]] = []
    with Session(engine) as session:
        query = select(ChunkGeneration).where(
            ChunkGeneration.bid_id == bid_id,
            ChunkGeneration.active.is_(True),
            ChunkGeneration.status.in_([Status.completed, Status.partially_completed]),
        )
        if generation_ids is not None:
            query = query.where(ChunkGeneration.id.in_(generation_ids))
        result = []
        for generation in session.scalars(query.order_by(ChunkGeneration.source_path)).all():
            rows = session.execute(
                select(DocumentChunk, ChunkContent)
                .join(ChunkContent, ChunkContent.id == DocumentChunk.content_id)
                .where(DocumentChunk.generation_id == generation.id)
                .order_by(DocumentChunk.sequence)
            ).all()
            occurrences = []
            try:
                for chunk, content in rows:
                    occurrence = ChunkOccurrence.model_validate(
                        {**chunk.provenance, "normalized_text": content.text}
                    )
                    if occurrence.occurrence_id != chunk.id or occurrence.content_id != content.id:
                        raise ValueError
                    occurrences.append(occurrence)
            except Exception:
                failures.append(
                    {"generation_id": str(generation.id), "code": "invalid_chunk_input"}
                )
                continue
            result.append(
                GenerationInput(
                    generation_id=generation.id,
                    bid_id=generation.bid_id,
                    document_id=generation.document_id,
                    relative_path=generation.source_path,
                    status=generation.status.value,
                    occurrences=occurrences,
                )
            )
        return result, failures
