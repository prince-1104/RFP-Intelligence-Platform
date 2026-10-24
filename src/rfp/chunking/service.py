import io
import time
import uuid
from functools import partial

from sqlalchemy import delete, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from rfp.chunking.engine import chunk_bytes, chunk_document, generation_id
from rfp.core.config import Settings
from rfp.db.models import (
    ChunkContent,
    ChunkGeneration,
    Document,
    DocumentChunk,
    ProcessingJob,
    Status,
)
from rfp.ingestion.service import STATES, TERMINAL, bid_lock, download_source, storage_call
from rfp.schemas.chunks import ChunkConfig, ChunkingResult, sha256
from rfp.schemas.parsed import ParsedDocument, ParseStatus
from rfp.storage.r2 import Storage, StorageError, TransientStorageError


def latest_documents(session: Session, bid_id: uuid.UUID) -> list[Document]:
    documents = session.scalars(
        select(Document)
        .where(Document.bid_id == bid_id)
        .order_by(Document.created_at.desc(), Document.id.desc())
    ).all()
    latest: dict[str, Document] = {}
    for document in documents:
        if document.relative_path:
            latest.setdefault(document.relative_path, document)
    return sorted(latest.values(), key=lambda document: document.relative_path or "")


def register_chunks(
    bid_id: uuid.UUID, settings: Settings, engine: Engine, storage: Storage
) -> tuple[uuid.UUID, dict]:
    config = settings.chunking
    with bid_lock(engine, bid_id), Session(engine) as session:
        documents = latest_documents(session, bid_id)
        generations = []
        accepted = []
        registration_failures = []
        for document in documents:
            if (
                document.status not in {Status.completed, Status.partially_completed}
                or not document.parsed_object_key
                or not document.relative_path
            ):
                registration_failures.append(
                    {"document_id": str(document.id), "code": "parsed_document_not_ready"}
                )
                continue
            if not document.parsed_artifact_sha256:
                # Phase 2 records created before 0003 are backfilled once, not reparsed.
                try:
                    payload = storage_call(
                        partial(
                            download_source,
                            storage,
                            document.parsed_object_key,
                            config.max_artifact_bytes,
                        ),
                        settings.ingestion_storage_attempts,
                    )
                    document.parsed_artifact_sha256 = sha256(payload)
                except (StorageError, ValueError):
                    registration_failures.append(
                        {"document_id": str(document.id), "code": "parsed_snapshot_unavailable"}
                    )
                    continue
            gid = generation_id(document.id, document.parsed_artifact_sha256, config.fingerprint)
            generation = session.get(ChunkGeneration, gid)
            if generation is None:
                generation = ChunkGeneration(
                    id=gid,
                    document_id=document.id,
                    bid_id=bid_id,
                    source_path=document.relative_path,
                    input_hash=document.parsed_artifact_sha256,
                    parsed_object_key=document.parsed_object_key,
                    config_hash=config.fingerprint,
                    config=config.model_dump(mode="json"),
                    status=Status.pending,
                )
                session.add(generation)
            generations.append(generation)
            accepted.append(document)
        if not generations:
            raise ValueError("no_parsed_documents_available")
        manifest = sorted(str(g.id) for g in generations) + sorted(
            failure["document_id"] for failure in registration_failures
        )
        jid = uuid.uuid5(bid_id, "chunk-job:" + ":".join(manifest))
        job = session.get(ProcessingJob, jid)
        if job is None:
            job = ProcessingJob(
                id=jid,
                bid_id=bid_id,
                kind="chunking",
                task_id=str(jid),
                parser_config={
                    "configuration": config.model_dump(mode="json"),
                    "registration_failures": registration_failures,
                },
                documents=accepted,
                chunk_generations=generations,
            )
            session.add(job)
        elif job.status in TERMINAL:
            retry = False
            for generation in job.chunk_generations:
                if generation.status == Status.failed:
                    generation.status = Status.pending
                    generation.error_code = None
                    retry = True
                elif generation.status == Status.completed and not generation.active:
                    retry = True
            if retry:
                job.status = Status.pending
                job.error_code = None
        session.commit()
        summary = chunk_job_summary(job)
        summary["skipped_sources"] = len(documents) - len(accepted)
        return jid, summary


def _activate(session: Session, generation: ChunkGeneration) -> None:
    session.execute(
        update(ChunkGeneration)
        .where(
            ChunkGeneration.bid_id == generation.bid_id,
            ChunkGeneration.source_path == generation.source_path,
            ChunkGeneration.id != generation.id,
            ChunkGeneration.active.is_(True),
        )
        .values(active=False)
    )
    session.flush()
    generation.active = True


def _persist(session: Session, generation: ChunkGeneration, result: ChunkingResult) -> None:
    # Content can race across different bids; conflict-safe insertion is enforced by the DB.
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    insert = pg_insert if session.get_bind().dialect.name == "postgresql" else sqlite_insert
    reused = 0
    for content in result.contents:
        statement = (
            insert(ChunkContent)
            .values(id=content.content_id, content_hash=content.content_hash, text=content.text)
            .on_conflict_do_nothing()
            .returning(ChunkContent.id)
        )
        inserted = session.scalar(statement)
        reused += int(inserted is None)
        stored = session.get(ChunkContent, content.content_id)
        if (
            stored is None
            or stored.text != content.text
            or stored.content_hash != content.content_hash
        ):
            raise ValueError("canonical_content_conflict")
    session.execute(delete(DocumentChunk).where(DocumentChunk.generation_id == generation.id))
    for occurrence in result.occurrences:
        session.add(
            DocumentChunk(
                id=occurrence.occurrence_id,
                generation_id=generation.id,
                document_id=generation.document_id,
                content_id=occurrence.content_id,
                sequence=occurrence.sequence,
                provenance=occurrence.model_dump(mode="json", exclude={"normalized_text"}),
            )
        )
    generation.chunk_count = len(result.occurrences)
    generation.reused_content_count = reused + len(result.occurrences) - len(result.contents)
    generation.normalized_content_hash = result.normalized_content_hash
    generation.status = (
        Status.completed
        if result.processing_status == ParseStatus.succeeded
        else Status.partially_completed
    )
    existing_active = session.scalar(
        select(ChunkGeneration.id).where(
            ChunkGeneration.bid_id == generation.bid_id,
            ChunkGeneration.source_path == generation.source_path,
            ChunkGeneration.active.is_(True),
            ChunkGeneration.id != generation.id,
        )
    )
    if generation.status == Status.completed or existing_active is None:
        _activate(session, generation)


def process_chunks(
    job_id: uuid.UUID,
    settings: Settings,
    engine: Engine,
    storage: Storage,
    final_attempt: bool = True,
    chunker=chunk_document,
) -> dict:
    with Session(engine) as lookup:
        job = lookup.get(ProcessingJob, job_id)
        if job is None or job.kind != "chunking":
            raise ValueError("chunk_job_not_found")
        bid_id = job.bid_id
    with bid_lock(engine, bid_id), Session(engine, expire_on_commit=False) as session:
        job = session.get(ProcessingJob, job_id)
        if job is None:
            raise ValueError("chunk_job_not_found")
        if job.status in TERMINAL:
            return chunk_job_summary(job)
        job.status = Status.processing
        job.error_code = None
        session.commit()
        transient = False
        started = time.monotonic()
        continuation = False
        batch_config = ChunkConfig.model_validate((job.parser_config or {}).get("configuration"))
        current = {
            document.relative_path: document for document in latest_documents(session, bid_id)
        }
        for generation in sorted(job.chunk_generations, key=lambda g: g.source_path):
            if generation.status == Status.failed:
                continue
            if (
                generation.status == Status.partially_completed
                or generation.status == Status.completed
                and generation.active
            ):
                # Already-active evidence needs neither activation writes nor batch budget.
                # Otherwise a short batch can repeatedly revisit old documents and starve new ones.
                continue
            if time.monotonic() - started > batch_config.batch_seconds:
                continuation = True
                break
            document = current.get(generation.source_path)
            if (
                document is None
                or document.id != generation.document_id
                or document.parsed_object_key != generation.parsed_object_key
                or document.parsed_artifact_sha256 != generation.input_hash
            ):
                if generation.status not in {Status.completed, Status.partially_completed}:
                    generation.status = Status.failed
                    generation.error_code = "stale_parsed_snapshot"
                    session.commit()
                continue
            if generation.status in {Status.completed, Status.partially_completed}:
                if generation.status == Status.completed:
                    _activate(session, generation)
                    session.commit()
                continue
            generation.status = Status.processing
            generation.error_code = None
            session.commit()
            try:
                config = ChunkConfig.model_validate(generation.config)
                payload = storage_call(
                    partial(
                        download_source,
                        storage,
                        generation.parsed_object_key,
                        config.max_artifact_bytes,
                    ),
                    settings.ingestion_storage_attempts,
                )
                if sha256(payload) != generation.input_hash:
                    raise ValueError("parsed_artifact_integrity_mismatch")
                parsed = ParsedDocument.model_validate_json(payload)
                if (
                    parsed.document_id != document.id
                    or parsed.bid_id != bid_id
                    or parsed.source_sha256 != document.source_sha256
                    or parsed.relative_path != generation.source_path
                ):
                    raise ValueError("parsed_provenance_mismatch")
                result = chunker(parsed, config, generation.input_hash)
                result = ChunkingResult.model_validate(result.model_dump())
                if result.generation_id != generation.id:
                    raise ValueError("chunk_generation_mismatch")
                output = chunk_bytes(result)
                key = (
                    f"bids/{bid_id}/documents/{document.id}/chunks/"
                    f"{generation.input_hash}/{generation.config_hash}.json"
                )

                def upload(key=key, output=output):
                    storage.upload(key, io.BytesIO(output), content_type="application/json")

                storage_call(upload, settings.ingestion_storage_attempts)
                generation.artifact_key = key
                generation.artifact_hash = sha256(output)
                if result.processing_status == ParseStatus.failed or result.skipped:
                    generation.status = Status.failed
                    generation.error_code = "empty_or_failed_chunk_generation"
                else:
                    _persist(session, generation, result)
                session.commit()
            except Exception as exc:
                # Roll back both content/occurrence insertion and activation on any failure.
                session.rollback()
                failed_generation = session.get(ChunkGeneration, generation.id)
                if failed_generation is None:
                    raise RuntimeError("chunk_generation_missing") from None
                failed_generation.status = Status.failed
                failed_generation.error_code = "chunk_processing_failed"
                if isinstance(exc, StorageError):
                    failed_generation.error_code = "storage_operation_failed"
                if isinstance(exc, TransientStorageError):
                    transient = True
                    if not final_attempt:
                        failed_generation.status = Status.pending
                session.commit()
        states = [generation.status for generation in job.chunk_generations]
        if continuation:
            job.status = Status.pending
        elif transient and not final_attempt:
            job.status = Status.pending
            job.error_code = "storage_retry_pending"
        elif (
            states
            and all(state == Status.completed for state in states)
            and not (job.parser_config or {}).get("registration_failures")
        ):
            job.status = Status.completed
        elif any(state in {Status.completed, Status.partially_completed} for state in states):
            job.status = Status.partially_completed
            job.error_code = "some_chunk_generations_incomplete"
        else:
            job.status = Status.failed
            job.error_code = "all_chunk_generations_failed"
        session.commit()
        summary = chunk_job_summary(job)
        summary["continuation_required"] = continuation
        if transient and not final_attempt:
            raise TransientStorageError("chunk_storage_retry_pending")
        return summary


def chunk_job_summary(job: ProcessingJob) -> dict:
    hashes: dict[str, list[str]] = {}
    for document in job.documents:
        if document.source_sha256:
            hashes.setdefault(document.source_sha256, []).append(str(document.id))
    return {
        "job_id": str(job.id),
        "bid_id": str(job.bid_id),
        "kind": "chunking",
        "state": STATES[job.status],
        "error_code": job.error_code,
        "registration_failures": (job.parser_config or {}).get("registration_failures", []),
        "source_duplicates": {key: sorted(ids) for key, ids in hashes.items() if len(ids) > 1},
        "generations": [
            {
                "generation_id": str(g.id),
                "document_id": str(g.document_id),
                "relative_path": g.source_path,
                "state": STATES[g.status],
                "active": g.active,
                "configuration_hash": g.config_hash,
                "parsed_artifact_sha256": g.input_hash,
                "artifact_key": g.artifact_key,
                "artifact_hash": g.artifact_hash,
                "chunk_count": g.chunk_count,
                "reused_content_count": g.reused_content_count,
                "error_code": g.error_code,
            }
            for g in sorted(job.chunk_generations, key=lambda g: g.source_path)
        ],
    }
