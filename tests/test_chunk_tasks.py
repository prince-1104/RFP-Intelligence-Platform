import uuid
from unittest.mock import Mock

import pytest
from celery.exceptions import Retry
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from test_chunk_persistence import ingest
from test_ingestion_service import database as database
from test_ingestion_service import folder as folder
from test_ingestion_service import store as store

from rfp.api.app import create_app
from rfp.api.ingestion import get_session
from rfp.chunking.service import process_chunks, register_chunks
from rfp.storage.r2 import TransientStorageError
from rfp.workers.celery_app import create_celery


def test_chunk_task_retries_and_disposes(settings, monkeypatch):
    task = create_celery(settings).tasks["rfp.chunk"]
    engine = Mock()
    monkeypatch.setattr("rfp.db.session.create_db_engine", lambda config: engine)
    monkeypatch.setattr("rfp.storage.r2.CloudflareR2Storage", lambda config: Mock())
    monkeypatch.setattr(
        "rfp.chunking.service.process_chunks", Mock(side_effect=TransientStorageError("secret"))
    )
    retry = Mock(side_effect=Retry("retry"))
    monkeypatch.setattr(task, "retry", retry)
    with pytest.raises(Retry):
        task.run(str(uuid.uuid4()))
    assert task.max_retries == 3 and task.acks_late
    assert "secret" not in str(retry.call_args)
    engine.dispose.assert_called_once()


def test_chunk_api_generations_and_job_summary(settings, database, folder, store):
    bid = ingest(folder, settings, database, store)
    job_id, summary = register_chunks(bid, settings, database, store)
    process_chunks(job_id, settings, database, store)
    app = create_app(settings, {})

    def sessions():
        with Session(database) as session:
            yield session

    app.dependency_overrides[get_session] = sessions
    gid = summary["generations"][0]["generation_id"]
    with TestClient(app) as client:
        job = client.get(f"/api/v1/jobs/{job_id}")
        assert job.json()["kind"] == "chunking" and job.json()["state"] == "succeeded"
        generation = client.get(f"/api/v1/chunk-generations/{gid}")
        assert generation.status_code == 200 and generation.json()["active"]
        assert generation.json()["artifact_hash"]
        assert client.get("/api/v1/chunk-generations/invalid").status_code == 422
        assert client.get(f"/api/v1/chunk-generations/{uuid.uuid4()}").status_code == 404
