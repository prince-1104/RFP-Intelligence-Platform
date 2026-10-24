import logging
import uuid
from typing import Annotated

import anyio
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session

from rfp.api.ingestion import get_session
from rfp.core.logging import log_event
from rfp.db.models import ProcessingJob
from rfp.schemas.parsed import DocumentType
from rfp.schemas.search import (
    ChunkType,
    IndexRequest,
    RetrievalMode,
    SearchFilters,
    SearchRequest,
    SearchResponse,
)
from rfp.search.errors import InvalidSearchRequest, SearchServiceError
from rfp.search.service import SearchService

router = APIRouter(prefix="/api/v1", tags=["search"])
logger = logging.getLogger("rfp.api.search")


def _error(message: str, code: str, status: int, retryable: bool = False) -> SearchServiceError:
    error = SearchServiceError(message, code)
    error.status, error.retryable = status, retryable
    return error


def get_search_service(request: Request) -> SearchService:
    state = request.app.state
    if state.search_service is None:
        state.search_service = state.search_service_factory()
    return state.search_service


async def _in_thread(request: Request, function, *args):
    """Blocking inference/Qdrant work runs on a bounded thread pool, never the event loop."""
    timeout = request.app.state.settings.search.inference_timeout_seconds
    try:
        with anyio.fail_after(timeout):
            return await anyio.to_thread.run_sync(
                function, *args, limiter=request.app.state.search_limiter, abandon_on_cancel=True
            )
    except TimeoutError:
        raise _error("Search timed out", "search_timeout", 503, True) from None


async def _search(request: Request, body: SearchRequest) -> SearchResponse:
    service = await _in_thread(request, get_search_service, request)
    return await _in_thread(request, service.search, body)


@router.get("/search", response_model=SearchResponse)
async def search_get(
    request: Request,
    q: Annotated[str, Query(min_length=1, max_length=10000)],
    top_k: Annotated[int | None, Query(ge=1)] = None,
    bid_id: uuid.UUID | None = None,
    document_id: uuid.UUID | None = None,
    doc_type: DocumentType | None = None,
    addendum_number: Annotated[int | None, Query(ge=0)] = None,
    chunk_type: ChunkType | None = None,
    mode: RetrievalMode = RetrievalMode.hybrid_rerank,
):
    filters = SearchFilters(
        bid_ids=[bid_id] if bid_id else [],
        document_ids=[document_id] if document_id else [],
        doc_types=[doc_type] if doc_type else [],
        addendum_numbers=[addendum_number] if addendum_number is not None else [],
        chunk_types=[chunk_type] if chunk_type else [],
    )
    try:
        body = SearchRequest(query=q, top_k=top_k, mode=mode, filters=filters)
    except ValidationError:
        raise InvalidSearchRequest() from None
    return await _search(request, body)


@router.post("/search", response_model=SearchResponse)
async def search_post(request: Request, body: SearchRequest):
    return await _search(request, body)


@router.post("/index", status_code=202)
async def schedule_index(request: Request, body: IndexRequest):
    from rfp.ingestion.service import IngestionBusy
    from rfp.search.jobs import register_index_job

    state = request.app.state
    try:
        job_id, summary = await anyio.to_thread.run_sync(
            register_index_job, body.bid_id, state.settings, state.engine
        )
    except ValueError:
        raise _error(
            "Bid has no active chunk generations to index", "no_active_chunk_generations", 409
        ) from None
    except IngestionBusy:
        raise _error("Bid is being processed; retry later", "bid_busy", 409, True) from None
    try:
        await anyio.to_thread.run_sync(state.index_dispatcher, job_id)
        summary["dispatch"] = "queued"
    except Exception:
        # Registration is committed; the job can be redispatched with `dispatch-job`.
        log_event(logger, "index_dispatch_failed", logging.ERROR, job_id=str(job_id))
        raise _error(
            "Job registered but could not be queued; retry or use dispatch-job",
            "dispatch_failed",
            503,
            True,
        ) from None
    return JSONResponse(status_code=202, content=summary)


@router.get("/index/jobs/{job_id}")
def get_index_job(job_id: uuid.UUID, session: Annotated[Session, Depends(get_session)]):
    from rfp.search.jobs import KIND, index_job_summary

    job = session.get(ProcessingJob, job_id)
    if job is None or job.kind != KIND:
        raise HTTPException(404)
    return index_job_summary(session, job)


@router.get("/index/status")
async def index_status(request: Request):
    service = await _in_thread(request, get_search_service, request)
    return await _in_thread(request, service.diagnostics)
