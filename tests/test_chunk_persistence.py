import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from test_ingestion_service import database as database
from test_ingestion_service import folder as folder
from test_ingestion_service import store as store

from rfp.chunking.service import process_chunks, register_chunks
from rfp.db.models import (
    ChunkContent,
    ChunkGeneration,
    Document,
    DocumentChunk,
    ProcessingJob,
    Status,
)
from rfp.ingestion.parser import parse_document
from rfp.ingestion.service import process_job, register_folder
from rfp.schemas.chunks import ChunkingResult
from rfp.storage.r2 import StorageError, TransientStorageError


def ingest(folder, settings, database, store):
    job_id, summary = register_folder(folder, settings, database, store)
    process_job(job_id, settings, database, store, parser=parse_document)
    return uuid.UUID(summary["bid_id"])


def test_persist_dedup_and_idempotency(folder, settings, database, store):
    (folder / "copy.html").write_bytes((folder / "a.html").read_bytes())
    bid_id = ingest(folder, settings, database, store)
    job_id, summary = register_chunks(bid_id, settings, database, store)
    assert summary["source_duplicates"]
    result = process_chunks(job_id, settings, database, store)
    assert result["state"] == "succeeded"
    generations = result["generations"]
    assert all(g["active"] for g in generations)
    for generation in generations:
        artifact = ChunkingResult.model_validate_json(store.objects[generation["artifact_key"]])
        assert artifact.document_id == uuid.UUID(generation["document_id"])
    with Session(database) as session:
        contents = session.scalar(select(func.count()).select_from(ChunkContent))
        occurrences = session.scalar(select(func.count()).select_from(DocumentChunk))
        assert occurrences == 2 * contents
    store.download.reset_mock()
    store.upload.reset_mock()
    same, _ = register_chunks(bid_id, settings, database, store)
    assert same == job_id
    assert process_chunks(job_id, settings, database, store)["state"] == "succeeded"
    store.download.assert_not_called()
    store.upload.assert_not_called()


def test_new_document_is_only_processed_document(folder, settings, database, store):
    bid_id = ingest(folder, settings, database, store)
    first_job, _ = register_chunks(bid_id, settings, database, store)
    process_chunks(first_job, settings, database, store)
    (folder / "new.html").write_text("<h1>Additional requirements</h1><p>Retain warranty.</p>")
    ingest(folder, settings, database, store)
    store.download.reset_mock()
    second_job, _ = register_chunks(bid_id, settings, database, store)
    assert second_job != first_job
    assert process_chunks(second_job, settings, database, store)["state"] == "succeeded"
    assert store.download.call_count == 1


def test_changed_source_revision_supersedes_only_after_success(folder, settings, database, store):
    bid_id = ingest(folder, settings, database, store)
    old_job, old_summary = register_chunks(bid_id, settings, database, store)
    process_chunks(old_job, settings, database, store)
    old_id = uuid.UUID(old_summary["generations"][0]["generation_id"])
    with Session(database) as session:
        old_doc = session.scalar(select(Document).where(Document.bid_id == bid_id))
        old_doc.created_at = datetime(2020, 1, 1, tzinfo=UTC)
        session.commit()
    (folder / "a.html").write_text("<h1>Equipment</h1><p>Updated quantity 25 model PX-421.</p>")
    ingest(folder, settings, database, store)
    new_job, summary = register_chunks(bid_id, settings, database, store)
    new_id = uuid.UUID(summary["generations"][0]["generation_id"])
    assert new_id != old_id
    with Session(database) as session:
        assert session.get(ChunkGeneration, old_id).active
    assert process_chunks(new_job, settings, database, store)["state"] == "succeeded"
    with Session(database) as session:
        assert not session.get(ChunkGeneration, old_id).active
        assert session.get(ChunkGeneration, new_id).active
        assert (
            session.scalar(
                select(func.count())
                .select_from(DocumentChunk)
                .where(DocumentChunk.generation_id == old_id)
            )
            > 0
        )


def test_failed_replacement_preserves_active_and_operator_retry(folder, settings, database, store):
    bid_id = ingest(folder, settings, database, store)
    first_job, summary = register_chunks(bid_id, settings, database, store)
    process_chunks(first_job, settings, database, store)
    old_id = uuid.UUID(summary["generations"][0]["generation_id"])
    changed = settings.model_copy(
        update={"chunking": settings.chunking.model_copy(update={"table_row_limit": 2})}
    )
    second_job, summary = register_chunks(bid_id, changed, database, store)
    new_id = uuid.UUID(summary["generations"][0]["generation_id"])
    upload = store.upload.side_effect
    store.upload.side_effect = StorageError("private-secret-url")
    failure = process_chunks(second_job, changed, database, store)
    assert failure["state"] == "failed" and "private-secret" not in str(failure)
    with Session(database) as session:
        assert session.get(ChunkGeneration, old_id).active
        assert not session.get(ChunkGeneration, new_id).active
    store.upload.side_effect = upload
    retry_job, _ = register_chunks(bid_id, changed, database, store)
    assert retry_job == second_job
    assert process_chunks(second_job, changed, database, store)["state"] == "succeeded"
    with Session(database) as session:
        assert not session.get(ChunkGeneration, old_id).active
        assert session.get(ChunkGeneration, new_id).active


def test_transient_and_document_partial_failure(folder, settings, database, store):
    (folder / "b.html").write_text("<p>Independent document content.</p>")
    bid_id = ingest(folder, settings, database, store)
    job_id, summary = register_chunks(bid_id, settings, database, store)
    download = store.download.side_effect

    def unavailable(key, target):
        if key.endswith("missing"):
            raise TransientStorageError("private-secret")
        return download(key, target)

    bad_id = uuid.UUID(summary["generations"][1]["generation_id"])
    with Session(database) as session:
        bad = session.get(ChunkGeneration, bad_id)
        document = session.get(Document, bad.document_id)
        bad.parsed_object_key += "missing"
        document.parsed_object_key = bad.parsed_object_key
        session.commit()
    store.download.side_effect = unavailable
    settings = settings.model_copy(update={"ingestion_storage_attempts": 1})
    with pytest.raises(TransientStorageError):
        process_chunks(job_id, settings, database, store, final_attempt=False)
    with Session(database) as session:
        assert session.get(ProcessingJob, job_id).status == Status.pending
    result = process_chunks(job_id, settings, database, store, final_attempt=True)
    assert result["state"] == "partially_succeeded"
    assert "private-secret" not in str(result)


def test_stale_snapshot_not_activated(folder, settings, database, store):
    bid_id = ingest(folder, settings, database, store)
    job_id, summary = register_chunks(bid_id, settings, database, store)
    with Session(database) as session:
        doc = session.scalar(select(Document).where(Document.bid_id == bid_id))
        doc.parsed_artifact_sha256 = "0" * 64
        session.commit()
    result = process_chunks(job_id, settings, database, store)
    assert result["state"] == "failed"
    assert result["generations"][0]["error_code"] == "stale_parsed_snapshot"


def test_active_generation_uniqueness_constraint(folder, settings, database, store):
    bid_id = ingest(folder, settings, database, store)
    job_id, summary = register_chunks(bid_id, settings, database, store)
    process_chunks(job_id, settings, database, store)
    with Session(database) as session:
        old = session.get(ChunkGeneration, uuid.UUID(summary["generations"][0]["generation_id"]))
        session.add(
            ChunkGeneration(
                id=uuid.uuid4(),
                bid_id=bid_id,
                document_id=old.document_id,
                source_path=old.source_path,
                input_hash=old.input_hash,
                config_hash="other",
                config=old.config,
                parsed_object_key=old.parsed_object_key,
                active=True,
                status=Status.completed,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_failed_database_commit_rolls_back_activation(
    folder, settings, database, store, monkeypatch
):
    from rfp.chunking import service

    bid_id = ingest(folder, settings, database, store)
    first, summary = register_chunks(bid_id, settings, database, store)
    process_chunks(first, settings, database, store)
    old_id = uuid.UUID(summary["generations"][0]["generation_id"])
    changed = settings.model_copy(
        update={"chunking": settings.chunking.model_copy(update={"table_row_limit": 1})}
    )
    second, _ = register_chunks(bid_id, changed, database, store)
    original = service._persist

    def broken(session, generation, result):
        original(session, generation, result)
        session.flush()
        raise RuntimeError("simulated database boundary")

    monkeypatch.setattr(service, "_persist", broken)
    assert process_chunks(second, changed, database, store)["state"] == "failed"
    with Session(database) as session:
        assert session.get(ChunkGeneration, old_id).active


def test_legacy_hash_backfill_and_one_bad_source(folder, settings, database, store):
    (folder / "b.html").write_text("<p>Another legacy source.</p>")
    bid = ingest(folder, settings, database, store)
    with Session(database) as session:
        docs = session.scalars(
            select(Document).where(Document.bid_id == bid).order_by(Document.relative_path)
        ).all()
        for doc in docs:
            doc.parsed_artifact_sha256 = None
        bad_key = docs[1].parsed_object_key
        session.commit()
    download = store.download.side_effect

    def unavailable(key, target):
        if key == bad_key:
            raise StorageError("private-provider-url")
        return download(key, target)

    store.download.side_effect = unavailable
    job, summary = register_chunks(bid, settings, database, store)
    assert summary["registration_failures"] and "private-provider" not in str(summary)
    result = process_chunks(job, settings, database, store)
    assert result["state"] == "partially_succeeded"


def test_partial_replacement_does_not_replace_complete_active(folder, settings, database, store):
    from rfp.chunking.engine import chunk_document
    from rfp.schemas.parsed import ParseStatus

    bid = ingest(folder, settings, database, store)
    first, summary = register_chunks(bid, settings, database, store)
    process_chunks(first, settings, database, store)
    old_id = uuid.UUID(summary["generations"][0]["generation_id"])
    changed = settings.model_copy(
        update={"chunking": settings.chunking.model_copy(update={"table_row_limit": 1})}
    )
    second, summary = register_chunks(bid, changed, database, store)

    def partial(parsed, config, digest):
        result = chunk_document(parsed, config, digest)
        result.processing_status = ParseStatus.partially_succeeded
        return result

    result = process_chunks(second, changed, database, store, chunker=partial)
    assert result["state"] == "partially_succeeded"
    with Session(database) as session:
        assert session.get(ChunkGeneration, old_id).active
        assert not session.get(
            ChunkGeneration, uuid.UUID(summary["generations"][0]["generation_id"])
        ).active


def test_active_documents_do_not_consume_incremental_batch(
    folder, settings, database, store, monkeypatch
):
    from rfp.chunking import service

    settings = settings.model_copy(
        update={"chunking": settings.chunking.model_copy(update={"batch_seconds": 1})}
    )
    bid = ingest(folder, settings, database, store)
    old_job, _ = register_chunks(bid, settings, database, store)
    process_chunks(old_job, settings, database, store)
    (folder / "new.html").write_text("<p>Only the new document should consume processing time.</p>")
    ingest(folder, settings, database, store)
    new_job, _ = register_chunks(bid, settings, database, store)
    clock = [0.0]
    activate = service._activate

    def slow_activation(session, generation):
        clock[0] += 2
        activate(session, generation)

    monkeypatch.setattr(service.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(service, "_activate", slow_activation)
    assert process_chunks(new_job, settings, database, store)["state"] == "succeeded"
