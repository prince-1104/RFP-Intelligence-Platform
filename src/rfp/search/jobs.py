"""Production indexing jobs on the shared ProcessingJob/Celery infrastructure."""

import logging
import time
import uuid
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from rfp.core.config import Settings
from rfp.core.logging import log_event
from rfp.db.models import ChunkGeneration, ChunkIndexState, ProcessingJob, Status
from rfp.ingestion.service import STATES, TERMINAL, bid_lock
from rfp.search.errors import SearchServiceError
from rfp.search.indexing import IndexingService, load_active_generations
from rfp.search.models import configured_profile_key

logger = logging.getLogger("rfp.search.jobs")
KIND = "indexing"
OUTCOME_STATUS = {
    "completed": Status.completed,
    "unchanged": Status.completed,
    "failed": Status.failed,
    "pending": Status.pending,
}
TRANSIENT_CODES = {"vector_store_unavailable", "embedding_cache_unavailable"}


class IndexRetryPending(Exception):
    """Transient vector-store/cache failure; Celery retries the same job."""


def state_id(generation_id: uuid.UUID, profile_fingerprint: str) -> uuid.UUID:
    return uuid.uuid5(generation_id, f"index-state:{profile_fingerprint}")


def register_index_job(
    bid_id: uuid.UUID, settings: Settings, engine: Engine
) -> tuple[uuid.UUID, dict]:
    key = configured_profile_key(settings.search, settings.qdrant_collection)
    with bid_lock(engine, bid_id), Session(engine) as session:
        generations = session.scalars(
            select(ChunkGeneration)
            .where(
                ChunkGeneration.bid_id == bid_id,
                ChunkGeneration.active.is_(True),
                ChunkGeneration.status.in_([Status.completed, Status.partially_completed]),
            )
            .order_by(ChunkGeneration.source_path)
        ).all()
        if not generations:
            raise ValueError("no_active_chunk_generations")
        manifest = ":".join(sorted(str(g.id) for g in generations))
        job_id = uuid.uuid5(bid_id, f"index-job:{key}:{manifest}")
        job = session.get(ProcessingJob, job_id)
        if job is None:
            job = ProcessingJob(
                id=job_id,
                bid_id=bid_id,
                kind=KIND,
                task_id=str(job_id),
                parser_config={
                    "configured_profile_key": key,
                    "collection_base": settings.qdrant_collection,
                    "dense_model": settings.search.dense_model,
                    "sparse_model": settings.search.sparse_model,
                },
                chunk_generations=list(generations),
            )
            session.add(job)
        elif job.status in {Status.failed, Status.partially_completed}:
            # Explicit re-registration retries failed generations; completed ones are reused.
            job.status = Status.pending
            job.error_code = None
        session.commit()
        return job_id, index_job_summary(session, job)


def process_index_job(
    job_id: uuid.UUID,
    settings: Settings,
    engine: Engine,
    service_factory: Callable[[], IndexingService],
    final_attempt: bool = True,
) -> dict:
    with Session(engine) as lookup:
        job = lookup.get(ProcessingJob, job_id)
        if job is None or job.kind != KIND:
            raise ValueError("index_job_not_found")
        bid_id = job.bid_id
    with bid_lock(engine, bid_id):
        with Session(engine, expire_on_commit=False) as session:
            job = session.get(ProcessingJob, job_id)
            if job is None:
                raise ValueError("index_job_not_found")
            if job.status in TERMINAL:
                # Repeated delivery of a finished job has no side effects.
                return index_job_summary(session, job)
            job.status = Status.processing
            job.error_code = None
            session.commit()
            wanted = [g.id for g in job.chunk_generations]
        # Model loading, inference and network uploads run without an open DB transaction.
        try:
            service = service_factory()
            profile = service.runtime.profile()
            collection = service.collection().name
        except SearchServiceError as exc:
            with Session(engine, expire_on_commit=False) as session:
                job = session.get(ProcessingJob, job_id)
                assert job is not None
                retry = exc.retryable and not final_attempt
                job.status = Status.pending if retry else Status.failed
                job.error_code = "index_retry_pending" if retry else exc.code
                session.commit()
                summary = index_job_summary(session, job)
            log_event(logger, "index_job_setup_failed", logging.ERROR, code=exc.code)
            if retry:
                raise IndexRetryPending("index_retry_pending") from None
            return summary
        with Session(engine) as session:
            done = set(
                session.scalars(
                    select(ChunkIndexState.generation_id).where(
                        ChunkIndexState.generation_id.in_(wanted),
                        ChunkIndexState.profile_fingerprint == profile.fingerprint,
                        ChunkIndexState.status == Status.completed,
                    )
                ).all()
            )
        pending = [g for g in wanted if g not in done]
        generations, input_failures = (
            load_active_generations(engine, bid_id, pending) if pending else ([], [])
        )
        loaded = {g.generation_id for g in generations}
        inactive = [
            g
            for g in pending
            if g not in loaded and str(g) not in {f["generation_id"] for f in input_failures}
        ]
        transient = False
        report = None
        error_code = None
        try:
            if generations:
                deadline = time.monotonic() + settings.search.index_batch_seconds
                report = service.index(generations, run_id=str(job_id), deadline=deadline)
        except SearchServiceError as exc:
            error_code = exc.code
            transient = exc.retryable
        with Session(engine, expire_on_commit=False) as session:
            job = session.get(ProcessingJob, job_id)
            assert job is not None
            config = dict(job.parser_config or {})
            config.update(
                {
                    "embedding_profile": profile.fingerprint,
                    "collection": collection,
                    "input_failures": input_failures,
                    "inactive_generations": [str(g) for g in inactive],
                }
            )
            job.parser_config = config

            def record(generation_id, document_id, status, code=None, outcome=None):
                identifier = state_id(generation_id, profile.fingerprint)
                state = session.get(ChunkIndexState, identifier)
                if state is None:
                    state = ChunkIndexState(
                        id=identifier,
                        generation_id=generation_id,
                        bid_id=bid_id,
                        document_id=document_id,
                        profile_fingerprint=profile.fingerprint,
                        collection_name=collection,
                    )
                    session.add(state)
                state.status = status
                state.error_code = code
                if outcome is not None:
                    state.point_count = outcome.point_count
                    state.cache_hits = outcome.cache_hits
                    state.cache_misses = outcome.cache_misses
                    state.truncated_inputs = outcome.truncated_inputs
                    state.attempts = (state.attempts or 0) + outcome.attempts
                    state.timings = {k: round(v, 1) for k, v in outcome.timings_ms.items()}

            documents = {g.id: g.document_id for g in job.chunk_generations}
            for failure in input_failures:
                gid = uuid.UUID(failure["generation_id"])
                record(gid, documents[gid], Status.failed, failure["code"])
            for gid in inactive:
                record(gid, documents[gid], Status.failed, "generation_no_longer_active")
            if report is not None:
                for outcome in report.outcomes:
                    status = OUTCOME_STATUS[outcome.status]
                    if status == Status.failed and outcome.error_code in TRANSIENT_CODES:
                        status = Status.failed if final_attempt else Status.pending
                    record(
                        outcome.generation_id,
                        outcome.document_id,
                        status,
                        outcome.error_code,
                        outcome,
                    )
            elif error_code is not None:
                for generation in generations:
                    record(
                        generation.generation_id,
                        generation.document_id,
                        Status.pending if transient and not final_attempt else Status.failed,
                        error_code,
                    )
            session.flush()
            states = {
                s.generation_id: s.status
                for s in session.scalars(
                    select(ChunkIndexState).where(
                        ChunkIndexState.generation_id.in_(wanted),
                        ChunkIndexState.profile_fingerprint == profile.fingerprint,
                    )
                ).all()
            }
            continuation = bool(report and report.continuation_required)
            retry_failed = report is not None and any(
                o.error_code in TRANSIENT_CODES for o in report.outcomes
            )
            transient = transient or retry_failed
            values = [states.get(g, Status.pending) for g in wanted]
            if continuation:
                job.status = Status.pending
            elif transient and not final_attempt:
                job.status = Status.pending
                job.error_code = "index_retry_pending"
            elif values and all(v == Status.completed for v in values):
                job.status = Status.completed
            elif any(v == Status.completed for v in values):
                job.status = Status.partially_completed
                job.error_code = "some_generations_not_indexed"
            else:
                job.status = Status.failed
                job.error_code = error_code or "all_generations_failed"
            session.commit()
            summary = index_job_summary(session, job)
        summary["continuation_required"] = continuation
        log_event(
            logger,
            "index_job_processed",
            job_id=str(job_id),
            state=summary["state"],
            code=job.error_code,
        )
        if transient and not final_attempt and not continuation:
            raise IndexRetryPending("index_retry_pending")
        return summary


def index_job_summary(session: Session, job: ProcessingJob) -> dict:
    config = job.parser_config or {}
    profile = config.get("embedding_profile")
    states = {}
    if profile:
        states = {
            s.generation_id: s
            for s in session.scalars(
                select(ChunkIndexState).where(
                    ChunkIndexState.generation_id.in_([g.id for g in job.chunk_generations]),
                    ChunkIndexState.profile_fingerprint == profile,
                )
            ).all()
        }
    generations = []
    for generation in sorted(job.chunk_generations, key=lambda g: g.source_path):
        state = states.get(generation.id)
        generations.append(
            {
                "generation_id": str(generation.id),
                "document_id": str(generation.document_id),
                "relative_path": generation.source_path,
                "chunk_generation_active": generation.active,
                "state": STATES[state.status] if state else "queued",
                "point_count": state.point_count if state else 0,
                "cache_hits": state.cache_hits if state else 0,
                "cache_misses": state.cache_misses if state else 0,
                "truncated_inputs": state.truncated_inputs if state else 0,
                "attempts": state.attempts if state else 0,
                "error_code": state.error_code if state else None,
                "timings_ms": state.timings if state else None,
            }
        )
    return {
        "job_id": str(job.id),
        "bid_id": str(job.bid_id),
        "kind": KIND,
        "state": STATES[job.status],
        "error_code": job.error_code,
        "embedding_profile": profile,
        "collection": config.get("collection"),
        "dense_model": config.get("dense_model"),
        "sparse_model": config.get("sparse_model"),
        "input_failures": config.get("input_failures", []),
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
        "generations": generations,
    }
