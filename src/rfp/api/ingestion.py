import uuid
from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from rfp.db.models import Document, ProcessingJob
from rfp.ingestion.service import job_summary

router = APIRouter(prefix="/api/v1", tags=["ingestion"])


def get_session(request: Request) -> Iterator[Session]:
    with Session(request.app.state.engine) as session:
        yield session


@router.get("/jobs/{job_id}")
def get_job(job_id: uuid.UUID, session: Annotated[Session, Depends(get_session)]):
    job = session.get(ProcessingJob, job_id)
    if job is None:
        raise HTTPException(404)
    return job_summary(job)


@router.get("/documents/{document_id}")
def get_document(document_id: uuid.UUID, session: Annotated[Session, Depends(get_session)]):
    document = session.get(Document, document_id)
    if document is None:
        raise HTTPException(404)
    return {
        "document_id": str(document.id),
        "bid_id": str(document.bid_id),
        "relative_path": document.relative_path,
        "source_sha256": document.source_sha256,
        "source_object_key": document.object_key,
        "parsed_object_key": document.parsed_object_key,
        "parser_fingerprint": document.parser_fingerprint,
        "status": document.status,
        "error_code": document.error_code,
    }
