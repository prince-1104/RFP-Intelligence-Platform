import uuid
from unittest.mock import Mock, patch

import pytest
from celery.exceptions import Retry
from qdrant_client import QdrantClient
from search_fakes import MemoryCache, services
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_chunk_persistence import ingest
from test_ingestion_service import database as database
from test_ingestion_service import folder as folder
from test_ingestion_service import store as store

from rfp.chunking.service import process_chunks, register_chunks
from rfp.db.models import ChunkIndexState, ProcessingJob, Status
from rfp.schemas.search import SearchRequest
from rfp.search.errors import ModelUnavailable, QdrantUnavailable
from rfp.search.jobs import IndexRetryPending, process_index_job, register_index_job
from rfp.workers.celery_app import create_celery


@pytest.fixture
def chunked_bid(folder, settings, database, store):
    (folder / "b.html").write_text("<h1>Schedule</h1><p>Proposals due March 3 for PX-420.</p>")
    bid_id = ingest(folder, settings, database, store)
    job_id, _ = register_chunks(bid_id, settings, database, store)
    process_chunks(job_id, settings, database, store)
    return bid_id


@pytest.fixture
def fake_services(settings):
    client = QdrantClient(":memory:")
    cache = MemoryCache()
    indexer, search, _ = services(settings, client=client, cache=cache)
    return indexer, search


def test_register_is_deterministic_and_requires_generations(chunked_bid, settings, database):
    job_id, summary = register_index_job(chunked_bid, settings, database)
    again, _ = register_index_job(chunked_bid, settings, database)
    assert job_id == again and summary["kind"] == "indexing" and summary["state"] == "queued"
    assert len(summary["generations"]) == 2
    with pytest.raises(ValueError, match="no_active_chunk_generations"):
        register_index_job(uuid.uuid4(), settings, database)


def test_process_job_indexes_records_states_and_is_idempotent(
    chunked_bid, settings, database, fake_services
):
    indexer, search = fake_services
    job_id, _ = register_index_job(chunked_bid, settings, database)
    summary = process_index_job(job_id, settings, database, lambda: indexer)
    assert summary["state"] == "succeeded"
    assert summary["embedding_profile"] and summary["collection"]
    assert all(g["state"] == "succeeded" and g["point_count"] for g in summary["generations"])
    repeated = process_index_job(job_id, settings, database, Mock(side_effect=AssertionError))
    assert repeated["state"] == "succeeded"
    with Session(database) as session:
        assert session.scalar(select(func.count()).select_from(ChunkIndexState)) == 2
    response = search.search(SearchRequest(query="PX-420", filters={"bid_ids": [chunked_bid]}))
    assert response.results and response.results[0].citation.relative_path == "b.html"
    assert response.results[0].citation.page_start is None


def test_transient_failure_retries_then_fails_on_final_attempt(
    chunked_bid, settings, database, fake_services
):
    indexer, _ = fake_services
    job_id, _ = register_index_job(chunked_bid, settings, database)
    indexer.index([])
    indexer.sleep = lambda _: None
    with patch.object(indexer.client, "upsert", side_effect=QdrantUnavailable()):
        with pytest.raises(IndexRetryPending):
            process_index_job(job_id, settings, database, lambda: indexer, final_attempt=False)
        with Session(database) as session:
            job = session.get(ProcessingJob, job_id)
            assert job.status == Status.pending and job.error_code == "index_retry_pending"
        summary = process_index_job(job_id, settings, database, lambda: indexer)
    assert summary["state"] == "failed"
    # Explicit re-registration resets the failed job; completion reuses prior state.
    register_index_job(chunked_bid, settings, database)
    assert process_index_job(job_id, settings, database, lambda: indexer)["state"] == "succeeded"


def test_setup_failure_marks_job_failed(chunked_bid, settings, database):
    job_id, _ = register_index_job(chunked_bid, settings, database)
    summary = process_index_job(job_id, settings, database, Mock(side_effect=ModelUnavailable()))
    assert summary["state"] == "failed" and summary["error_code"] == "embedding_model_unavailable"


def test_unknown_job_rejected(settings, database):
    with pytest.raises(ValueError, match="index_job_not_found"):
        process_index_job(uuid.uuid4(), settings, database, Mock())


def test_index_task_retries_and_disposes(settings, monkeypatch):
    task = create_celery(settings).tasks["rfp.index"]
    engine = Mock()
    monkeypatch.setattr("rfp.db.session.create_db_engine", lambda config: engine)
    monkeypatch.setattr(
        "rfp.search.jobs.process_index_job", Mock(side_effect=IndexRetryPending("secret"))
    )
    retry = Mock(side_effect=Retry("retry"))
    monkeypatch.setattr(task, "retry", retry)
    with pytest.raises(Retry):
        task.run(str(uuid.uuid4()))
    assert task.max_retries == 3 and task.acks_late
    assert "secret" not in str(retry.call_args)
    engine.dispose.assert_called_once()


def test_index_task_requeues_continuation(settings, monkeypatch):
    task = create_celery(settings).tasks["rfp.index"]
    monkeypatch.setattr("rfp.db.session.create_db_engine", lambda config: Mock())
    monkeypatch.setattr(
        "rfp.search.jobs.process_index_job",
        Mock(return_value={"state": "queued", "continuation_required": True}),
    )
    requeue = Mock()
    monkeypatch.setattr(task, "apply_async", requeue)
    job_id = str(uuid.uuid4())
    task.run(job_id)
    assert requeue.call_args.kwargs["args"] == [job_id]
