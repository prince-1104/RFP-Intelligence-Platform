"""Standalone hybrid search over indexed source occurrences. Returns evidence, never answers."""

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from qdrant_client import QdrantClient, models

from rfp.core.config import Settings
from rfp.core.logging import log_event
from rfp.schemas.search import (
    EvidenceResult,
    IdentifierMatch,
    ProfileInfo,
    QueryDiagnostics,
    RetrievalMode,
    ScoreComponents,
    SearchFilters,
    SearchRequest,
    SearchResponse,
)
from rfp.search.collection import DENSE_VECTOR, SPARSE_VECTOR, CollectionManager, qdrant_call
from rfp.search.errors import IndexUnavailable, InvalidSearchRequest, SearchServiceError
from rfp.search.fusion import reciprocal_rank_fusion
from rfp.search.identifiers import matched_identifiers
from rfp.search.models import ModelRuntime, RerankScores, validate_dense
from rfp.search.payload import citation_from_payload
from rfp.search.query import QueryPlan, plan_query

logger = logging.getLogger("rfp.search.service")
MAX_DUPLICATE_CITATIONS = 200


@dataclass
class Candidate:
    point_id: str
    payload: dict[str, Any]
    dense_score: float | None = None
    dense_rank: int | None = None
    sparse_score: float | None = None
    sparse_rank: int | None = None
    identifier_rank: int | None = None
    rrf_score: float | None = None
    reranker_score: float | None = None
    identifier_bonus: float | None = None
    final_score: float = 0.0
    matched: list[str] = field(default_factory=list)


def build_filter(filters: SearchFilters, profile_fingerprint: str) -> models.Filter:
    must: list[Any] = [
        models.FieldCondition(key="index_active", match=models.MatchValue(value=True)),
        models.FieldCondition(
            key="profile_fingerprint", match=models.MatchValue(value=profile_fingerprint)
        ),
    ]
    keyword_filters: dict[str, list[str]] = {
        "bid_id": [str(value) for value in filters.bid_ids],
        "document_id": [str(value) for value in filters.document_ids],
        "doc_type": [value.value for value in filters.doc_types],
        "chunk_type": list(filters.chunk_types),
        "relative_path": list(filters.relative_paths),
    }
    for key, values in keyword_filters.items():
        if values:
            must.append(models.FieldCondition(key=key, match=models.MatchAny(any=values)))
    if filters.addendum_numbers:
        must.append(
            models.FieldCondition(
                key="addendum_number", match=models.MatchAny(any=list(filters.addendum_numbers))
            )
        )
    return models.Filter(must=must)


def _with(base: models.Filter, condition: Any) -> models.Filter:
    return models.Filter(must=[*(base.must or []), condition])  # type: ignore[misc]


class SearchService:
    def __init__(self, runtime: ModelRuntime, client: QdrantClient, settings: Settings):
        self.runtime = runtime
        self.client = client
        self.settings = settings
        self.config = settings.search
        self._verified: set[str] = set()

    def _manager(self) -> CollectionManager:
        manager = CollectionManager(
            self.client, self.settings.qdrant_collection, self.runtime.profile()
        )
        if manager.name not in self._verified:
            manager.check_server()
            manager.verify()
            self._verified.add(manager.name)
        return manager

    def profile_info(self, manager: CollectionManager, rerank: bool) -> ProfileInfo:
        profile = self.runtime.profile()
        return ProfileInfo(
            embedding_profile=profile.fingerprint,
            collection=manager.name,
            dense_model=profile.dense_model,
            sparse_model=profile.sparse_model,
            reranker_model=self.config.reranker_model if rerank else None,
            fastembed_version=profile.fastembed_version,
        )

    def diagnostics(self) -> dict[str, Any]:
        manager = CollectionManager(
            self.client, self.settings.qdrant_collection, self.runtime.profile()
        )
        return {**manager.diagnostics(), "models_loaded": self.runtime.loaded()}

    def search(self, request: SearchRequest, rerank_depth: int | None = None) -> SearchResponse:
        started = time.monotonic()
        timings: dict[str, float] = {}
        top_k = request.top_k or self.config.default_top_k
        if top_k > self.config.max_top_k:
            raise InvalidSearchRequest(
                f"top_k must not exceed {self.config.max_top_k}", "top_k_too_large"
            )
        plan = plan_query(request.query, self.config)
        mode = request.mode
        try:
            manager = self._manager()
        except IndexUnavailable:
            self._verified.clear()
            raise
        query_filter = build_filter(request.filters, self.runtime.profile().fingerprint)
        warnings: list[str] = []
        degraded = False

        use_dense = mode in {RetrievalMode.dense, RetrievalMode.hybrid, RetrievalMode.hybrid_rerank}
        use_sparse = mode != RetrievalMode.dense
        use_identifiers = mode in {RetrievalMode.hybrid, RetrievalMode.hybrid_rerank}

        # Lazy model loading is timed separately so per-stage timings reflect inference only.
        timer = time.monotonic()
        needed = {
            "dense": use_dense or (use_identifiers and plan.identifier_terms),
            "sparse": use_sparse,
            "reranker": mode == RetrievalMode.hybrid_rerank,
        }
        missing = [
            name for name, used in needed.items() if used and name not in self.runtime.loaded()
        ]
        for name in missing:
            try:
                getattr(self.runtime, name)
            except SearchServiceError:
                if name == "dense" or not self.config.allow_degraded:
                    raise
        if missing:
            timings["model_load"] = (time.monotonic() - timer) * 1000

        timer = time.monotonic()
        dense_vector = None
        if use_dense or (use_identifiers and plan.identifier_terms):
            dense = self.runtime.dense
            dense_vector = validate_dense(
                self.runtime.infer(
                    lambda: dense.embed_query(plan.dense_query), "dense_query_failed"
                ),
                dense.dim,
            )
        sparse_vector = None
        if use_sparse:
            sparse = self.runtime.sparse
            try:
                sparse_vector = self.runtime.infer(
                    lambda: sparse.embed_query(plan.sparse_query), "sparse_query_failed"
                )
            except SearchServiceError as exc:
                if mode == RetrievalMode.sparse or not self.config.allow_degraded:
                    raise
                degraded = True
                warnings.append(f"sparse_branch_unavailable:{exc.code}")
            if sparse_vector is not None and not sparse_vector.indices:
                warnings.append("sparse_query_has_no_terms")
        timings["query_embedding"] = (time.monotonic() - timer) * 1000

        requests: list[tuple[str, models.QueryRequest]] = []
        depth = self.config.candidate_depth
        if use_dense and dense_vector is not None:
            requests.append(
                (
                    "dense",
                    models.QueryRequest(
                        query=dense_vector,
                        using=DENSE_VECTOR,
                        filter=query_filter,
                        limit=depth,
                        with_payload=True,
                    ),
                )
            )
        if sparse_vector is not None and sparse_vector.indices:
            requests.append(
                (
                    "sparse",
                    models.QueryRequest(
                        query=models.SparseVector(
                            indices=list(sparse_vector.indices), values=list(sparse_vector.values)
                        ),
                        using=SPARSE_VECTOR,
                        filter=query_filter,
                        limit=depth,
                        with_payload=True,
                    ),
                )
            )
        if use_identifiers and plan.identifier_terms and dense_vector is not None:
            # Exact keyword safeguard: candidates that literally contain a query identifier,
            # ordered semantically, under the identical metadata filter.
            requests.append(
                (
                    "identifier",
                    models.QueryRequest(
                        query=dense_vector,
                        using=DENSE_VECTOR,
                        filter=_with(
                            query_filter,
                            models.FieldCondition(
                                key="identifiers",
                                match=models.MatchAny(any=plan.identifier_terms),
                            ),
                        ),
                        limit=depth,
                        with_payload=True,
                    ),
                )
            )

        timer = time.monotonic()
        responses = []
        if requests:
            responses = qdrant_call(
                lambda: self.client.query_batch_points(
                    manager.name, requests=[request for _, request in requests]
                )
            )
        timings["vector_search"] = (time.monotonic() - timer) * 1000

        candidates: dict[str, Candidate] = {}
        rankings: dict[str, list[str]] = {}
        for (branch, _), response in zip(requests, responses, strict=True):
            ranking = []
            points = response.points
            if branch == "sparse":
                # Zero-score sparse hits share no weighted term and must not earn RRF credit.
                points = [point for point in points if point.score > 0]
            for rank, point in enumerate(points, start=1):
                identifier = str(point.id)
                candidate = candidates.setdefault(
                    identifier, Candidate(identifier, dict(point.payload or {}))
                )
                if branch == "dense":
                    candidate.dense_score, candidate.dense_rank = float(point.score), rank
                elif branch == "sparse":
                    candidate.sparse_score, candidate.sparse_rank = float(point.score), rank
                else:
                    candidate.identifier_rank = rank
                ranking.append(identifier)
            rankings[branch] = ranking
        counts = {branch: len(ranking) for branch, ranking in rankings.items()}
        for candidate in candidates.values():
            candidate.matched = matched_identifiers(
                plan.identifiers, list(candidate.payload.get("identifiers") or [])
            )

        timer = time.monotonic()
        ordered = self._order(mode, rankings, candidates)
        timings["fusion"] = (time.monotonic() - timer) * 1000
        counts["fused"] = len(ordered)

        identifier_missing = (
            plan.identifiers
            and self.config.identifier_policy == "require_evidence"
            and not any(candidate.matched for candidate in ordered)
        )
        if mode == RetrievalMode.hybrid_rerank and ordered and not identifier_missing:
            timer = time.monotonic()
            try:
                depth = rerank_depth or self.config.rerank_depth
                ordered, output, unique = self._rerank(plan, ordered, depth)
                counts["reranked"] = min(len(ordered), depth)
                counts["rerank_inputs"] = unique
                if output.windowed:
                    warnings.append("rerank_input_windowed")
                    counts["rerank_windowed"] = output.windowed
                if output.truncated:
                    warnings.append("rerank_input_truncated")
                    counts["rerank_truncated"] = output.truncated
            except SearchServiceError as exc:
                if not self.config.allow_degraded:
                    raise
                degraded = True
                warnings.append(f"reranker_unavailable:{exc.code}")
            timings["rerank"] = (time.monotonic() - timer) * 1000

        if identifier_missing:
            warnings.append("identifier_evidence_not_found")
            ordered = []
        if not ordered:
            warnings.append("no_results")

        results = self._results(ordered, top_k, query_filter, manager, plan)
        timings["total"] = (time.monotonic() - started) * 1000
        result = SearchResponse(
            query=QueryDiagnostics(
                original=plan.original,
                normalized=plan.normalized,
                dense_query=plan.dense_query,
                sparse_query=plan.sparse_query,
                expansions=plan.expansions,
                identifiers=plan.identifiers,
            ),
            mode=mode,
            top_k=top_k,
            filters=request.filters,
            results=results,
            result_count=len(results),
            degraded=degraded,
            warnings=warnings,
            profile=self.profile_info(manager, mode == RetrievalMode.hybrid_rerank),
            candidate_counts=counts,
            timings_ms={key: round(value, 2) for key, value in timings.items()},
        )
        log_event(
            logger,
            "search_completed",
            mode=mode.value,
            query_hash=plan.query_hash,
            query_chars=len(plan.original),
            expansions=len(plan.expansions),
            identifiers=len(plan.identifiers),
            filters=sorted(key for key, value in request.filters.model_dump().items() if value),
            candidate_counts=counts,
            result_count=len(results),
            degraded=degraded,
            profile=self.runtime.profile().short,
            timings_ms=result.timings_ms,
        )
        return result

    def _order(
        self, mode: RetrievalMode, rankings: dict[str, list[str]], candidates: dict[str, Candidate]
    ) -> list[Candidate]:
        if mode == RetrievalMode.dense:
            ordered = [candidates[i] for i in rankings.get("dense", [])]
            for candidate in ordered:
                candidate.final_score = candidate.dense_score or 0.0
            return ordered
        if mode == RetrievalMode.sparse:
            ordered = [candidates[i] for i in rankings.get("sparse", [])]
            for candidate in ordered:
                candidate.final_score = candidate.sparse_score or 0.0
            return ordered
        fused = reciprocal_rank_fusion(
            rankings,
            k=self.config.rrf_k,
            weights={
                "dense": self.config.dense_weight,
                "sparse": self.config.sparse_weight,
                "identifier": self.config.identifier_weight,
            },
        )
        ordered = []
        for identifier, score in fused:
            candidate = candidates[str(identifier)]
            candidate.rrf_score = score
            candidate.final_score = score
            ordered.append(candidate)
        return ordered

    def _rerank(
        self, plan: QueryPlan, ordered: list[Candidate], depth: int
    ) -> tuple[list[Candidate], RerankScores, int]:
        head = ordered[:depth]
        tail = ordered[depth:]
        reranker = self.runtime.reranker
        # Identical text scores identically, so each distinct text is scored once.
        texts = list(dict.fromkeys(c.payload.get("text", "") for c in head))
        output = self.runtime.infer(
            lambda: reranker.rerank(plan.normalized, texts), "reranker_failed"
        )
        if len(output.scores) != len(texts):
            raise SearchServiceError("Reranker output size mismatch", "reranker_failed")
        by_text = dict(zip(texts, output.scores, strict=True))
        scores = [by_text[c.payload.get("text", "")] for c in head]
        for candidate, score in zip(head, scores, strict=True):
            candidate.reranker_score = float(score)
            candidate.identifier_bonus = (
                self.config.identifier_rerank_bonus if candidate.matched else 0.0
            )
            candidate.final_score = candidate.reranker_score + candidate.identifier_bonus
        head.sort(key=lambda c: (-c.final_score, -(c.rrf_score or 0.0)))
        # Candidates beyond the rerank depth keep fused order and are never promoted above
        # reranked evidence; they are only used if top_k exceeds the rerank depth.
        return head + tail, output, len(texts)

    def _results(
        self,
        ordered: list[Candidate],
        top_k: int,
        query_filter: models.Filter,
        manager: CollectionManager,
        plan: QueryPlan,
    ) -> list[EvidenceResult]:
        groups: list[tuple[Candidate, list[dict[str, Any]]]] = []
        by_hash: dict[str, int] = {}
        for candidate in ordered:
            content_hash = candidate.payload.get("content_hash", candidate.point_id)
            if self.config.collapse_duplicates and content_hash in by_hash:
                groups[by_hash[content_hash]][1].append(candidate.payload)
                continue
            if len(groups) >= top_k:
                continue
            by_hash[content_hash] = len(groups)
            groups.append((candidate, []))
        if self.config.collapse_duplicates and groups:
            self._attach_all_citations(groups, query_filter, manager)
        results = []
        for rank, (candidate, extra) in enumerate(groups, start=1):
            payload = candidate.payload
            results.append(
                EvidenceResult(
                    rank=rank,
                    occurrence_id=payload["occurrence_id"],
                    content_id=payload["content_id"],
                    content_hash=payload["content_hash"],
                    text=payload.get("text", ""),
                    chunk_type=payload["chunk_type"],
                    citation=citation_from_payload(payload),
                    additional_citations=[citation_from_payload(item) for item in extra],
                    scores=ScoreComponents(
                        dense_score=candidate.dense_score,
                        dense_rank=candidate.dense_rank,
                        sparse_score=candidate.sparse_score,
                        sparse_rank=candidate.sparse_rank,
                        identifier_rank=candidate.identifier_rank,
                        rrf_score=candidate.rrf_score,
                        reranker_score=candidate.reranker_score,
                        identifier_bonus=candidate.identifier_bonus,
                        final_score=candidate.final_score,
                    ),
                    identifier_match=IdentifierMatch(
                        query_identifiers=plan.identifiers, matched=candidate.matched
                    ),
                )
            )
        return results

    def _attach_all_citations(
        self,
        groups: list[tuple[Candidate, list[dict[str, Any]]]],
        query_filter: models.Filter,
        manager: CollectionManager,
    ) -> None:
        """Every filter-valid occurrence of a returned text stays cited, even beyond depth."""
        hashes = [group[0].payload["content_hash"] for group in groups]
        points, _ = qdrant_call(
            lambda: self.client.scroll(
                manager.name,
                scroll_filter=_with(
                    query_filter,
                    models.FieldCondition(key="content_hash", match=models.MatchAny(any=hashes)),
                ),
                limit=MAX_DUPLICATE_CITATIONS,
                with_payload=True,
                with_vectors=False,
            )
        )
        index = {group[0].payload["content_hash"]: group for group in groups}
        for point in points:
            payload = dict(point.payload or {})
            group = index.get(payload.get("content_hash"))
            if group is None:
                continue
            seen = {group[0].payload["occurrence_id"]} | {p["occurrence_id"] for p in group[1]}
            if payload["occurrence_id"] not in seen:
                group[1].append(payload)
        for _, extra in groups:
            extra.sort(key=lambda p: (p["relative_path"], p.get("sequence") or 0))
