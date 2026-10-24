import hashlib
import io
import time
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from functools import partial
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from rfp.core.config import Settings
from rfp.db.models import Bid, Document, ProcessingJob, Status
from rfp.ingestion.discovery import DiscoveryError, discover, read_source
from rfp.ingestion.options import ParserOptions
from rfp.ingestion.parser import artifact_bytes, parse_isolated
from rfp.schemas.parsed import ParsedDocument, ParseStatus
from rfp.storage.r2 import Storage, StorageError, TransientStorageError

TERMINAL = {Status.completed, Status.partially_completed, Status.failed}
STATES = {
    Status.pending: "queued",
    Status.processing: "running",
    Status.completed: "succeeded",
    Status.partially_completed: "partially_succeeded",
    Status.failed: "failed",
}


class IngestionBusy(Exception):
    pass


def storage_call(operation: Callable[[], Any], attempts: int, sleep=time.sleep):
    for attempt in range(attempts):
        try:
            return operation()
        except TransientStorageError:
            if attempt + 1 == attempts:
                raise
            sleep(min(2**attempt, 8))


@contextmanager
def bid_lock(engine: Engine, bid_id: uuid.UUID):
    """Session advisory lock spans per-document commits; released even after worker death."""
    if engine.dialect.name != "postgresql":
        # SQLite is used only by sequential unit tests, never production Settings.
        yield
        return
    number = int.from_bytes(hashlib.sha256(bid_id.bytes).digest()[:8], "big", signed=True)
    with engine.connect() as connection:
        acquired = connection.execute(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": number}
        ).scalar()
        connection.commit()
        if not acquired:
            raise IngestionBusy("bid_is_processing")
        try:
            yield
        finally:
            connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": number})
            connection.commit()


def register_folder(
    folder: Path,
    settings: Settings,
    engine: Engine,
    storage: Storage,
    bid_identifier: str | None = None,
) -> tuple[uuid.UUID, dict]:
    discovery = discover(folder, settings, bid_identifier)
    if not discovery.files:
        raise DiscoveryError("no_supported_documents")
    options = ParserOptions.from_settings(settings)
    manifest = "\n".join(f"{source.relative_path}:{source.sha256}" for source in discovery.files)
    job_id = uuid.uuid5(discovery.bid_id, f"{options.fingerprint}\n{manifest}")
    with bid_lock(engine, discovery.bid_id), Session(engine) as session:
        existing = session.get(ProcessingJob, job_id)
        if existing:
            return job_id, job_summary(existing)
        # R2 writes precede the database transaction commit. A failed registration leaves no
        # misleading queued rows; stable source objects can be reused on a retry.
        bid = session.get(Bid, discovery.bid_id)
        if bid is None:
            bid = Bid(
                id=discovery.bid_id,
                title=discovery.bid_identifier,
                external_id=discovery.bid_identifier,
            )
            session.add(bid)
            session.flush()
        job = ProcessingJob(
            id=job_id,
            bid_id=bid.id,
            kind="ingestion",
            task_id=str(job_id),
            parser_config=options.model_dump(),
            status=Status.pending,
        )
        bid.status = Status.pending
        session.add(job)
        for source in discovery.files:
            document_id = source.document_id(bid.id)
            document = session.get(Document, document_id)
            if document is None:
                data = read_source(source, settings.ingestion_max_file_bytes)
                key = f"bids/{bid.id}/documents/{document_id}/source{source.path.suffix.lower()}"

                def upload(data=data, key=key, source=source):
                    storage.upload(
                        key,
                        io.BytesIO(data),
                        content_type=source.content_type,
                        metadata={"sha256": source.sha256},
                    )

                storage_call(upload, settings.ingestion_storage_attempts)
                document = Document(
                    id=document_id,
                    bid_id=bid.id,
                    filename=source.path.name,
                    relative_path=source.relative_path,
                    source_sha256=source.sha256,
                    source_size=source.size,
                    object_key=key,
                    content_type=source.content_type,
                )
                session.add(document)
            job.documents.append(document)
        session.commit()
        return job_id, {
            **job_summary(job),
            "ignored_files": len(discovery.ignored),
            "discovery_warnings": discovery.warnings,
        }


class LimitedBuffer(io.BytesIO):
    def __init__(self, limit: int):
        super().__init__()
        self.limit = limit

    def write(self, data) -> int:
        if self.tell() + len(data) > self.limit:
            raise ValueError("download_size_limit")
        return super().write(data)


def download_source(storage: Storage, key: str, limit: int) -> bytes:
    with LimitedBuffer(limit) as target:
        storage.download(key, target)
        return target.getvalue()


def process_job(
    job_id: uuid.UUID,
    settings: Settings,
    engine: Engine,
    storage: Storage,
    parser=parse_isolated,
    final_attempt: bool = True,
) -> dict:
    with Session(engine) as lookup:
        job = lookup.get(ProcessingJob, job_id)
        if job is None or job.kind != "ingestion":
            raise ValueError("ingestion_job_not_found")
        bid_id = job.bid_id
    with bid_lock(engine, bid_id), Session(engine, expire_on_commit=False) as session:
        job = session.get(ProcessingJob, job_id)
        if job is None:
            raise ValueError("ingestion_job_not_found")
        if job.status in TERMINAL:
            return job_summary(job)
        options = ParserOptions.model_validate(job.parser_config)
        bid = session.get(Bid, bid_id)
        if bid is None or not job.documents:
            raise ValueError("invalid_ingestion_job")
        bid.status = Status.processing
        job.status = Status.processing
        job.error_code = None
        session.commit()
        storage_failed = False
        started = time.monotonic()
        continuation = False
        for document in sorted(job.documents, key=lambda doc: doc.relative_path or ""):
            if (
                document.status in {Status.completed, Status.partially_completed}
                and document.parser_fingerprint == options.fingerprint
            ):
                continue
            if time.monotonic() - started > 240:
                continuation = True
                break
            document.status = Status.processing
            document.error_code = None
            session.commit()
            try:
                data = storage_call(
                    partial(download_source, storage, document.object_key, options.max_file_bytes),
                    settings.ingestion_storage_attempts,
                )
                if hashlib.sha256(data).hexdigest() != document.source_sha256:
                    raise ValueError("source_integrity_mismatch")
                result = parser(
                    data, document.relative_path, bid_id, options, settings.tesseract_cmd
                )
                result = ParsedDocument.model_validate(result.model_dump())
                if (
                    result.document_id != document.id
                    or result.source_sha256 != document.source_sha256
                ):
                    raise ValueError("parser_provenance_mismatch")
                key = f"bids/{bid_id}/documents/{document.id}/parsed/{options.fingerprint}.json"
                payload = artifact_bytes(result)

                def upload_artifact(key=key, payload=payload, digest=document.source_sha256):
                    storage.upload(
                        key,
                        io.BytesIO(payload),
                        content_type="application/json",
                        metadata={"source-sha256": digest},
                    )

                storage_call(upload_artifact, settings.ingestion_storage_attempts)
                document.parsed_object_key = key
                document.parsed_artifact_sha256 = hashlib.sha256(payload).hexdigest()
                document.parser_fingerprint = options.fingerprint
                document.status = {
                    ParseStatus.succeeded: Status.completed,
                    ParseStatus.partially_succeeded: Status.partially_completed,
                    ParseStatus.failed: Status.failed,
                }[result.processing_status]
                if document.status == Status.failed:
                    document.error_code = "parse_failed"
            except TransientStorageError:
                storage_failed = True
                document.status = Status.failed
                document.error_code = "storage_operation_failed"
            except StorageError:
                document.status = Status.failed
                document.error_code = "storage_operation_failed"
            except Exception:
                # Isolate one failed document and persist a sanitized code. No exception
                # contents, URLs or source text are stored. Other documents continue.
                document.status = Status.failed
                document.error_code = "document_processing_failed"
            session.commit()
        states = [document.status for document in job.documents]
        if continuation:
            job.status = Status.pending
        elif storage_failed and not final_attempt:
            job.status = Status.pending
            job.error_code = "storage_retry_pending"
        elif all(state == Status.completed for state in states):
            job.status = Status.completed
        elif any(state in {Status.completed, Status.partially_completed} for state in states):
            job.status = Status.partially_completed
            job.error_code = "some_documents_incomplete"
        else:
            job.status = Status.failed
            job.error_code = "all_documents_failed"
        session.commit()
        bid.status = job.status
        session.commit()
        summary = job_summary(job)
        summary["continuation_required"] = continuation
        if storage_failed and not final_attempt:
            raise TransientStorageError("storage_retry_pending")
        return summary


def job_summary(job: ProcessingJob) -> dict:
    if job.kind == "chunking":
        from rfp.chunking.service import chunk_job_summary

        return chunk_job_summary(job)
    return {
        "job_id": str(job.id),
        "bid_id": str(job.bid_id),
        "state": STATES[job.status],
        "error_code": job.error_code,
        "documents": [
            {
                "document_id": str(doc.id),
                "relative_path": doc.relative_path,
                "state": STATES[doc.status],
                "error_code": doc.error_code,
                "parsed_object_key": doc.parsed_object_key,
            }
            for doc in sorted(job.documents, key=lambda doc: doc.relative_path or "")
        ],
    }
