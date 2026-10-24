import time
import uuid
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from search_fakes import BID_A, BID_B, generation, sample_bid, services
from sqlalchemy.orm import Session
from test_chunk_persistence import ingest
from test_ingestion_service import database as database
from test_ingestion_service import folder as folder
from test_ingestion_service import store as store

from rfp.api.app import create_app
from rfp.api.ingestion import get_session
from rfp.chunking.service import process_chunks, register_chunks
from rfp.search.errors import QdrantUnavailable
from rfp.search.jobs import process_index_job


@pytest.fixture
def indexed(settings):
    indexer, search, client = services(settings)
    assert indexer.index([generation(r) for r in sample_bid(BID_A) + sample_bid(BID_B)]).succeeded
    return indexer, search


@pytest.fixture
def api(settings, indexed, database):
    app = create_app(settings, {})
    app.state.search_service_factory = lambda: indexed[1]
    app.state.index_dispatcher = Mock()

    def sessions():
        with Session(database) as session:
            yield session

    app.dependency_overrides[get_session] = sessions
    app.state.engine = database
    with TestClient(app) as client:
        yield client, app


def test_get_search_with_filters_and_citations(api):
    client, _ = api
    response = client.get(
        "/api/v1/search",
        params={"q": "PX-420", "bid_id": str(BID_A), "mode": "hybrid", "top_k": 3},
        headers={"X-Request-ID": "corr-123"},
    )
    assert response.status_code == 200 and response.headers["X-Request-ID"] == "corr-123"
    body = response.json()
    assert body["mode"] == "hybrid" and body["top_k"] == 3
    top = body["results"][0]
    assert top["citation"]["relative_path"] == "specs/pumps.pdf"
    assert top["citation"]["page_start"] == 1 and top["citation"]["bid_id"] == str(BID_A)
    assert body["filters"]["bid_ids"] == [str(BID_A)]
    assert body["profile"]["embedding_profile"]


def test_post_search_with_multiple_filters(api):
    client, _ = api
    response = client.post(
        "/api/v1/search",
        json={
            "query": "pre-bid meeting",
            "top_k": 2,
            "mode": "hybrid_rerank",
            "filters": {"bid_ids": [str(BID_B)], "chunk_types": ["prose"]},
        },
    )
    assert response.status_code == 200
    top = response.json()["results"][0]
    assert top["citation"]["page_start"] is None and top["citation"]["html_locators"]
    assert top["citation"]["bid_id"] == str(BID_B)


@pytest.mark.parametrize(
    ("method", "kwargs"),
    [
        ("get", {"params": {"q": "x", "top_k": 0}}),
        ("get", {"params": {"q": "x", "mode": "magic"}}),
        ("get", {"params": {"q": "x", "bid_id": "not-a-uuid"}}),
        ("get", {"params": {"q": "x", "top_k": 500}}),
        ("post", {"json": {"query": ""}}),
        ("post", {"json": {"query": "x", "filters": {"unknown": 1}}}),
    ],
)
def test_validation_errors_use_envelope(api, method, kwargs):
    client, _ = api
    response = getattr(client, method)("/api/v1/search", **kwargs)
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] in {"validation_error", "invalid_search_request", "top_k_too_large"}
    assert error["request_id"]


def test_query_too_long_and_symbols_only(api):
    client, _ = api
    assert client.get("/api/v1/search", params={"q": "y" * 2000}).json()["error"]["code"] == (
        "query_too_long"
    )
    assert client.get("/api/v1/search", params={"q": "?!"}).json()["error"]["code"] == (
        "invalid_query"
    )


def test_service_errors_are_redacted_and_retryable(api, settings):
    client, app = api
    service = Mock()
    service.search.side_effect = QdrantUnavailable()
    app.state.search_service = service
    response = client.get("/api/v1/search", params={"q": "pumps"})
    assert response.status_code == 503 and response.headers["Retry-After"] == "5"
    assert response.json()["error"]["code"] == "vector_store_unavailable"
    service.search.side_effect = RuntimeError(f"traceback with {settings.qdrant_url} secret")
    crashed = client.get("/api/v1/search", params={"q": "pumps"})
    assert crashed.status_code == 500
    assert "secret" not in crashed.text and "traceback" not in crashed.text


def test_search_timeout(api, settings):
    client, app = api
    settings.search.inference_timeout_seconds = 1
    service = Mock()
    service.search.side_effect = lambda request: time.sleep(1.5)
    app.state.search_service = service
    response = client.get("/api/v1/search", params={"q": "pumps"})
    assert response.status_code == 503 and response.json()["error"]["code"] == "search_timeout"


def test_index_status(api):
    client, _ = api
    body = client.get("/api/v1/index/status").json()
    assert body["exists"] and body["points_count"] > 0


def test_index_schedule_status_and_errors(api, settings, database, folder, store, indexed):
    client, app = api
    bid_id = ingest(folder, settings, database, store)
    job_id, _ = register_chunks(bid_id, settings, database, store)
    process_chunks(job_id, settings, database, store)

    assert client.post("/api/v1/index", json={"bid_id": str(uuid.uuid4())}).status_code == 409
    scheduled = client.post("/api/v1/index", json={"bid_id": str(bid_id)})
    assert scheduled.status_code == 202
    body = scheduled.json()
    assert body["dispatch"] == "queued" and body["state"] == "queued"
    app.state.index_dispatcher.assert_called_once()
    index_job = uuid.UUID(body["job_id"])

    process_index_job(index_job, settings, database, lambda: indexed[0])
    status = client.get(f"/api/v1/index/jobs/{index_job}")
    assert status.status_code == 200
    assert status.json()["state"] == "succeeded"
    assert status.json()["generations"][0]["point_count"] > 0
    assert client.get(f"/api/v1/index/jobs/{uuid.uuid4()}").status_code == 404
    assert client.get(f"/api/v1/index/jobs/{job_id}").status_code == 404  # chunking job

    app.state.index_dispatcher.side_effect = RuntimeError("broker password")
    failed = client.post("/api/v1/index", json={"bid_id": str(bid_id)})
    assert failed.status_code == 503 and failed.json()["error"]["code"] == "dispatch_failed"
    assert "password" not in failed.text
