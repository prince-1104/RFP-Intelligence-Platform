"""Workbench API contracts. These tests do not call a model or Qdrant."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rfp.agents.schemas import REQUIRED_FIELDS
from rfp.api.workbench import create_workbench_app
from rfp.workbench.catalog import qdrant_json
from rfp.workbench.errors import WorkbenchError
from rfp.workbench.guards import assert_writable_collection
from rfp.workbench.jobs import JobStore


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data" / "sample").mkdir(parents=True)
    (tmp_path / "data" / "sample" / "note.pdf").write_bytes(b"%PDF-1.4")
    app = create_workbench_app(jobs=JobStore())
    app.state.list_collections = lambda url: [
        {"base": "bid_documents", "physical": "bid_documents__abc123abc123"}
    ]
    app.state.scroll_points = lambda url, physical: [
        {
            "payload": {
                "bid_id": "5809c088-8514-58c3-a0e5-4d9e72adab0b",
                "document_id": "doc-1",
                "original_file_name": "devices.pdf",
                "relative_path": "devices.pdf",
                "doc_type": "rfp",
            }
        }
    ]
    app.state.ask = lambda *args: {
        "bid_id": "Bid1",
        "question": "When?",
        "status": "not_found",
        "answer": "Not found in the cited evidence.",
        "citations": [],
        "claims": [],
        "validation": {"passed": True, "attempts": 1, "checks": [], "reasons": []},
        "trace_id": "trace-1",
    }
    app.state.compare = lambda *args: {"status": "partial", "bid_ids": args[0]}
    app.state.audit = lambda: {
        "available": True,
        "problem_count": 0,
        "extractions": [],
        "qa": {},
        "retrieval": {},
    }
    app.state.extract = lambda *args: {"exit_code": 0, "fields": list(REQUIRED_FIELDS)}
    app.state.ingest = lambda *args: {"exit_code": 0}
    app.state.evaluate = lambda *args: (_ for _ in ()).throw(
        AssertionError("evaluation must not run without confirm")
    )
    with TestClient(app) as api:
        yield api


def test_capabilities_keep_the_twenty_field_contract(client):
    body = client.get("/api/v1/workbench/capabilities").json()
    assert body["fields"] == list(REQUIRED_FIELDS)
    assert body["qa_statuses"] == ["answered", "not_found", "conflict"]
    assert body["relations"] == ["identical", "different", "one_sided"]
    assert "page_furniture" in body and "table_prose" in body


def test_health_does_not_call_postgres_healthy(client, monkeypatch):
    monkeypatch.setattr(
        "rfp.api.workbench.catalog.qdrant_ready",
        lambda *args, **kwargs: True,
    )
    body = client.get("/api/v1/workbench/health").json()
    assert body["postgres"] == "not_used"
    assert body["redis"] == "not_used"
    assert body["qdrant"] == "healthy"
    assert "key" not in body


def test_remote_qdrant_is_refused():
    with pytest.raises(WorkbenchError) as caught:
        qdrant_json("https://example.cloud.qdrant.io", "/collections")
    assert caught.value.code == "unsafe_search_target"


def test_existing_collections_cannot_be_write_targets():
    with pytest.raises(WorkbenchError) as caught:
        assert_writable_collection("bid_documents")
    assert caught.value.code == "collection_protected"
    assert assert_writable_collection("workbench_demo") == "workbench_demo"


def test_ingest_requires_confirmation_and_rejects_protected_names(client):
    refused = client.post(
        "/api/v1/workbench/ingest",
        json={
            "confirm": True,
            "collection": "bid_documents",
            "bid_id": "Sample",
            "path": "data/sample",
        },
    )
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "collection_protected"
    unconfirmed = client.post(
        "/api/v1/workbench/ingest",
        json={
            "confirm": False,
            "collection": "workbench_demo",
            "bid_id": "Sample",
            "path": "data/sample",
        },
    )
    assert unconfirmed.status_code == 409
    assert unconfirmed.json()["error"]["code"] == "confirmation_required"


def test_ingest_rejects_paths_outside_the_workspace(client):
    response = client.post(
        "/api/v1/workbench/ingest",
        json={
            "confirm": True,
            "collection": "workbench_demo",
            "bid_id": "Sample",
            "path": "../outside",
        },
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_path"


def test_upload_rejects_unsupported_types(client):
    response = client.post("/api/v1/workbench/uploads?filename=notes.exe", content=b"MZ")
    assert response.status_code == 415


def test_confirmed_ingest_uses_the_injected_runner(client):
    response = client.post(
        "/api/v1/workbench/ingest",
        json={
            "confirm": True,
            "collection": "workbench_demo",
            "bid_id": "Sample",
            "path": "data/sample",
        },
    )
    assert response.status_code == 202
    job = response.json()
    assert job["status"] in {"queued", "running", "succeeded"}
    stored = client.get(f"/api/v1/workbench/jobs/{job['id']}")
    assert stored.status_code == 200


def test_ask_not_found_is_not_an_http_error(client):
    response = client.post(
        "/api/v1/workbench/ask",
        json={"collection": "bid_documents", "bid_id": "Bid1", "question": "When is it due?"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "not_found"


def test_ask_model_failure_is_not_reported_as_not_found(client):
    client.app.state.ask = lambda *args: (_ for _ in ()).throw(
        WorkbenchError("model_unavailable", "The model call failed.", 502)
    )
    response = client.post(
        "/api/v1/workbench/ask",
        json={"collection": "bid_documents", "bid_id": "Bid1", "question": "When is it due?"},
    )
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "model_unavailable"
    assert response.json()["error"]["code"] != "not_found"


def test_compare_rejects_more_than_four_bids(client):
    response = client.post(
        "/api/v1/workbench/compare",
        json={
            "collection": "bid_documents",
            "bids": ["A", "B", "C", "D", "E"],
            "question": "How do the warranties differ?",
        },
    )
    assert response.status_code == 422


def test_ask_rejects_a_qdrant_url_field(client):
    response = client.post(
        "/api/v1/workbench/ask",
        json={
            "collection": "bid_documents",
            "bid_id": "Bid1",
            "question": "When?",
            "qdrant_url": "https://example.cloud.qdrant.io",
        },
    )
    assert response.status_code == 422


def test_evaluation_get_does_not_start_a_run(client):
    response = client.get("/api/v1/workbench/evaluation")
    assert response.status_code == 200
    assert response.json()["snapshots"] == []


def test_audit_returns_the_injected_result_and_matrix_label(client, tmp_path: Path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "FINAL_REQUIREMENTS_AUDIT.md").write_text("| 1 | example |\n", encoding="utf-8")
    response = client.get("/api/v1/workbench/audit")
    body = response.json()
    assert body["problem_count"] == 0
    assert "does not re-score" in body["matrix"]["label"]


def test_bids_come_from_the_collection_payload(client):
    body = client.get("/api/v1/workbench/bids", params={"collection": "bid_documents"}).json()
    assert body["bids"][0]["documents"][0]["file"] == "devices.pdf"
    assert body["bids"][0]["label"]
