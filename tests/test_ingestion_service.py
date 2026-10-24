import json
import uuid
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from rfp.api.app import create_app
from rfp.api.ingestion import get_session
from rfp.db.models import Base, Bid, Document, ProcessingJob, Status
from rfp.ingestion.parser import parse_document
from rfp.ingestion.service import download_source, process_job, register_folder, storage_call
from rfp.schemas.parsed import ParsedDocument
from rfp.storage.r2 import StorageError, TransientStorageError


@pytest.fixture
def database():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def store():
    """Test-only in-memory mock implementing the existing storage protocol."""
    objects = {}
    storage = Mock()

    def upload(key, body, **kwargs):
        objects[key] = body.read()

    def download(key, target):
        target.write(objects[key])

    storage.upload.side_effect = upload
    storage.download.side_effect = download
    storage.objects = objects
    return storage


@pytest.fixture
def folder(tmp_path):
    source = tmp_path / "arbitrary-bid"
    source.mkdir()
    (source / "a.html").write_text("<h1>Equipment</h1><p>Model AB-123 quantity 20</p>")
    return source


def test_registration_processing_idempotency(folder, settings, database, store):
    job_id, queued = register_folder(folder, settings, database, store)
    assert queued["state"] == "queued"
    assert len(store.objects) == 1
    again, _ = register_folder(folder, settings, database, store)
    assert again == job_id
    result = process_job(job_id, settings, database, store, parser=parse_document)
    assert result["state"] == "succeeded"
    artifact_key = result["documents"][0]["parsed_object_key"]
    artifact = ParsedDocument.model_validate_json(store.objects[artifact_key])
    assert artifact.relative_path == "a.html" and artifact.parsed_pages[0].page_number is None
    calls = store.upload.call_count
    assert process_job(job_id, settings, database, store, parser=Mock())["state"] == "succeeded"
    assert store.upload.call_count == calls
    with Session(database) as session:
        assert session.scalar(select(func.count()).select_from(Document)) == 1
        assert session.get(ProcessingJob, job_id).status == Status.completed
        assert session.get(Bid, uuid.UUID(result["bid_id"])).status == Status.completed


def test_partial_and_failed_documents(folder, settings, database, store):
    (folder / "broken.pdf").write_bytes(b"invalid PDF")
    job_id, _ = register_folder(folder, settings, database, store)
    result = process_job(job_id, settings, database, store, parser=parse_document)
    assert result["state"] == "partially_succeeded"
    assert [doc["state"] for doc in result["documents"]] == ["succeeded", "failed"]
    assert result["documents"][1]["error_code"] == "parse_failed"
    assert result["documents"][1]["parsed_object_key"] in store.objects


def test_storage_failure_rolls_back_registration(folder, settings, database, store):
    store.upload.side_effect = StorageError("private provider detail")
    with pytest.raises(StorageError):
        register_folder(folder, settings, database, store)
    with Session(database) as session:
        assert session.scalar(select(func.count()).select_from(Document)) == 0
        assert session.scalar(select(func.count()).select_from(ProcessingJob)) == 0


def test_transient_retry_bounds_and_recovery(folder, settings, database, store, monkeypatch):
    operation = Mock(side_effect=TransientStorageError("secret"))
    sleeper = Mock()
    with pytest.raises(TransientStorageError):
        storage_call(operation, 3, sleep=sleeper)
    assert operation.call_count == 3
    assert [call.args[0] for call in sleeper.call_args_list] == [1, 2]
    permanent = Mock(side_effect=StorageError("no retry"))
    with pytest.raises(StorageError):
        storage_call(permanent, 3, sleep=sleeper)
    assert permanent.call_count == 1
    job_id, _ = register_folder(folder, settings, database, store)
    download = store.download.side_effect
    store.download.side_effect = TransientStorageError("private-detail")
    settings = settings.model_copy(update={"ingestion_storage_attempts": 1})
    with pytest.raises(TransientStorageError):
        process_job(job_id, settings, database, store, parser=parse_document, final_attempt=False)
    with Session(database) as session:
        assert session.get(ProcessingJob, job_id).status == Status.pending
    store.download.side_effect = download
    assert (
        process_job(job_id, settings, database, store, parser=parse_document)["state"]
        == "succeeded"
    )


def test_failure_redaction_and_integrity(folder, settings, database, store):
    job_id, _ = register_folder(folder, settings, database, store)
    first_key = next(iter(store.objects))
    store.objects[first_key] = b"tampered"
    result = process_job(job_id, settings, database, store, parser=Mock())
    assert result["state"] == "failed"
    assert result["documents"][0]["error_code"] == "document_processing_failed"
    assert "tampered" not in json.dumps(result)


def test_download_bounded(store):
    store.objects["key"] = b"x" * 20
    with pytest.raises(ValueError, match="download_size_limit"):
        download_source(store, "key", 10)


def test_job_and_document_api(folder, settings, database, store):
    job_id, _ = register_folder(folder, settings, database, store)
    app = create_app(settings, {})

    def sessions():
        with Session(database) as session:
            yield session

    app.dependency_overrides[get_session] = sessions
    with TestClient(app) as client:
        response = client.get(f"/api/v1/jobs/{job_id}")
        assert response.status_code == 200 and response.json()["state"] == "queued"
        document_id = response.json()["documents"][0]["document_id"]
        assert client.get(f"/api/v1/documents/{document_id}").json()["relative_path"] == "a.html"
        invalid = client.get("/api/v1/jobs/not-a-uuid")
        assert invalid.status_code == 422 and invalid.json()["error"]["code"] == "validation_error"
        missing = client.get(f"/api/v1/jobs/{uuid.uuid4()}")
        assert missing.status_code == 404 and missing.headers["X-Request-ID"]
