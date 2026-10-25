"""Local workbench API. It does not mount the production routers and does not open
Postgres, Redis, Celery or R2."""

import re
import uuid
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import Field

from rfp.agents.schemas import REQUIRED_FIELDS
from rfp.cli import LOCAL_QDRANT, local_settings
from rfp.core.logging import request_id
from rfp.schemas.parsed import Contract
from rfp.schemas.search import RetrievalMode
from rfp.workbench import catalog, operations, snapshots
from rfp.workbench.errors import WorkbenchError
from rfp.workbench.guards import (
    MAX_UPLOAD_BYTES,
    allowed_input_roots,
    assert_readable_collection,
    assert_writable_collection,
    resolve_source,
    safe_upload_name,
)
from rfp.workbench.jobs import JobStore

ORIGIN = "http://127.0.0.1:4000"


class AskBody(Contract):
    collection: str
    bid_id: str
    question: str = Field(min_length=1, max_length=1000)
    mode: RetrievalMode = RetrievalMode.hybrid_rerank
    top_k: int | None = Field(default=None, ge=1, le=20)


class CompareBody(Contract):
    collection: str
    bids: list[str] = Field(min_length=2, max_length=4)
    question: str = Field(min_length=1, max_length=1000)


class SearchBody(Contract):
    collection: str
    query: str = Field(min_length=1, max_length=10000)
    mode: RetrievalMode = RetrievalMode.hybrid_rerank
    bid_ids: list[str] = Field(default_factory=list, max_length=4)
    top_k: int | None = Field(default=None, ge=1, le=50)


class ConfirmedJob(Contract):
    confirm: bool
    collection: str
    bid_id: str = ""
    path: str = ""


def create_workbench_app(jobs: JobStore | None = None) -> FastAPI:
    settings = local_settings(LOCAL_QDRANT)
    app = FastAPI(title="RFP Intelligence Workbench", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[ORIGIN],
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-Request-ID"],
    )
    app.state.settings = settings
    app.state.qdrant_url = LOCAL_QDRANT
    app.state.jobs = jobs or JobStore(directory=Path("artifacts/workbench/jobs"))
    app.state.list_collections = catalog.list_collections
    app.state.scroll_points = catalog.scroll_points
    app.state.ask = operations.ask
    app.state.compare = operations.compare
    app.state.search = operations.search
    app.state.extract = operations.extract
    app.state.ingest = operations.ingest
    app.state.evaluate = operations.evaluate
    app.state.audit = operations.audit
    app.state.find_trace = operations.find_trace

    def error(status: int, code: str, message: str) -> JSONResponse:
        return JSONResponse(
            status_code=status,
            content={"error": {"code": code, "message": message, "request_id": request_id.get()}},
        )

    @app.middleware("http")
    async def correlate(request: Request, call_next):
        supplied = request.headers.get("X-Request-ID", "")
        correlation = (
            supplied if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", supplied) else uuid.uuid4().hex
        )
        token = request_id.set(correlation)
        try:
            try:
                response = await call_next(request)
            except Exception:
                response = error(500, "internal_error", "Internal server error")
            response.headers["X-Request-ID"] = correlation
            return response
        finally:
            request_id.reset(token)

    @app.exception_handler(WorkbenchError)
    async def workbench_error(request: Request, exc: WorkbenchError):
        return error(exc.status, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        return error(422, "validation_error", "Invalid request")

    @app.get("/api/v1/workbench/health")
    def health(request: Request):
        try:
            ready = catalog.qdrant_ready(request.app.state.qdrant_url)
        except WorkbenchError:
            ready = False
        qdrant = "healthy" if ready else "unavailable"
        cache = Path(settings.search.model_cache_dir)
        return {
            "status": "ready" if qdrant == "healthy" else "not_ready",
            "qdrant": qdrant,
            "postgres": "not_used",
            "redis": "not_used",
            "model_cache": "present" if cache.is_dir() else "missing",
            "qdrant_url": request.app.state.qdrant_url,
        }

    @app.get("/api/v1/workbench/capabilities")
    def capabilities():
        chunking = settings.chunking
        return {
            "modes": [mode.value for mode in RetrievalMode],
            "fields": list(REQUIRED_FIELDS),
            "compare_min": 2,
            "compare_max": 4,
            "qa_statuses": ["answered", "not_found", "conflict"],
            "comparison_statuses": ["answered", "partial", "not_found"],
            "relations": ["identical", "different", "one_sided"],
            "page_furniture": chunking.page_furniture,
            "table_prose": chunking.table_prose,
            "confidence": "heuristic_0_to_1",
        }

    @app.get("/api/v1/workbench/collections")
    def collections(request: Request):
        return {"collections": request.app.state.list_collections(request.app.state.qdrant_url)}

    def _physical(request: Request, base: str) -> str:
        name = assert_readable_collection(base)
        rows = request.app.state.list_collections(request.app.state.qdrant_url)
        matches = [row["physical"] for row in rows if row["base"] == name]
        if not matches:
            raise WorkbenchError(
                "collection_not_found", "That local collection is not present.", 404
            )
        return matches[0]

    @app.get("/api/v1/workbench/bids")
    def bids(request: Request, collection: str):
        physical = _physical(request, collection)
        points = request.app.state.scroll_points(request.app.state.qdrant_url, physical)
        return {
            "collection": assert_readable_collection(collection),
            "physical": physical,
            "bids": catalog.bids_from_points(points),
        }

    @app.post("/api/v1/workbench/search")
    def search(request: Request, body: SearchBody):
        collection = assert_readable_collection(body.collection)
        return request.app.state.search(
            body.query, collection, body.mode.value, body.bid_ids, body.top_k
        )

    @app.post("/api/v1/workbench/ask")
    def ask(request: Request, body: AskBody):
        collection = assert_readable_collection(body.collection)
        return request.app.state.ask(
            body.bid_id, body.question, collection, body.mode.value, body.top_k
        )

    @app.post("/api/v1/workbench/compare")
    def compare(request: Request, body: CompareBody):
        collection = assert_readable_collection(body.collection)
        return request.app.state.compare(body.bids, body.question, collection)

    def _require_confirm(body: ConfirmedJob) -> None:
        if body.confirm is not True:
            raise WorkbenchError(
                "confirmation_required",
                "This operation is expensive. Send confirm true to start it.",
                409,
            )

    @app.post("/api/v1/workbench/ingest", status_code=202)
    def ingest(request: Request, body: ConfirmedJob):
        _require_confirm(body)
        collection = assert_writable_collection(body.collection)
        if not body.bid_id or not body.path:
            raise WorkbenchError("invalid_path", "A bid id and a source path are required.")
        source = resolve_source(body.path, roots=allowed_input_roots())
        if not source.is_dir():
            raise WorkbenchError(
                "invalid_path", "Ingestion expects a folder of PDF and HTML files."
            )
        bid_id = body.bid_id
        job = request.app.state.jobs.submit(
            "ingest", lambda: request.app.state.ingest(source, bid_id, collection)
        )
        return job

    @app.post("/api/v1/workbench/uploads", status_code=201)
    async def upload(request: Request, filename: str):
        name = safe_upload_name(filename)
        body = await request.body()
        if len(body) > MAX_UPLOAD_BYTES:
            raise WorkbenchError("file_too_large", "The file exceeds the 50 MB limit.", 413)
        target_dir = Path("artifacts/workbench/uploads") / uuid.uuid4().hex
        target_dir.mkdir(parents=True, exist_ok=False)
        destination = target_dir / name
        destination.write_bytes(body)
        return {"path": destination.as_posix(), "bytes": len(body)}

    @app.post("/api/v1/workbench/extract", status_code=202)
    def extract(request: Request, body: ConfirmedJob):
        _require_confirm(body)
        collection = assert_readable_collection(body.collection)
        if not body.bid_id:
            raise WorkbenchError("invalid_bid_id", "A bid id is required.")
        bid_id = body.bid_id
        job = request.app.state.jobs.submit(
            "extract", lambda: request.app.state.extract(bid_id, collection)
        )
        return job

    @app.get("/api/v1/workbench/extractions")
    def extractions():
        return {"extractions": snapshots.extraction_runs()}

    @app.get("/api/v1/workbench/extractions/file")
    def extraction_file(source: str):
        payload = snapshots.read_extraction(source)
        if payload is None:
            raise WorkbenchError(
                "not_found", "That extraction file is not an allowed snapshot.", 404
            )
        return payload

    @app.get("/api/v1/workbench/traces/{trace_id}")
    def trace(request: Request, trace_id: str):
        payload = request.app.state.find_trace(trace_id)
        if payload is None:
            raise WorkbenchError("not_found", "No saved trace has that id.", 404)
        return payload

    @app.get("/api/v1/workbench/evaluation")
    def evaluation():
        return {"snapshots": snapshots.evaluation_snapshots()}

    @app.get("/api/v1/workbench/evaluation/detail")
    def evaluation_detail(source: str):
        payload = snapshots.evaluation_detail(source)
        if payload is None:
            raise WorkbenchError(
                "not_found", "That evaluation file is not an allowed snapshot.", 404
            )
        return payload

    @app.post("/api/v1/workbench/evaluation", status_code=202)
    def run_evaluation(request: Request, body: ConfirmedJob):
        _require_confirm(body)
        collection = assert_readable_collection(body.collection)
        job = request.app.state.jobs.submit(
            "evaluation", lambda: request.app.state.evaluate(collection)
        )
        return job

    @app.get("/api/v1/workbench/audit")
    def audit(request: Request):
        result = request.app.state.audit()
        result["matrix"] = snapshots.audit_matrix()
        return result

    @app.get("/api/v1/workbench/qa-snapshot")
    def qa_snapshot():
        payload = snapshots.qa_snapshot()
        if payload is None:
            return {"available": False, "kind": "saved_qa_snapshot"}
        return payload

    @app.get("/api/v1/workbench/overview")
    def overview(request: Request):
        try:
            collections = request.app.state.list_collections(request.app.state.qdrant_url)
        except WorkbenchError:
            collections = None
        latest = request.app.state.jobs.latest("ingest")
        return {
            "collections": collections,
            "last_ingest": latest,
            "evaluation": snapshots.evaluation_snapshots()[:1],
            "extractions": snapshots.extraction_runs()[:8],
            "qa": snapshots.qa_snapshot(),
        }

    @app.get("/api/v1/workbench/jobs/{job_id}")
    def job(request: Request, job_id: str):
        return request.app.state.jobs.get(job_id)

    return app
