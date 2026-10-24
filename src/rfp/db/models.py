import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Table,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Status(enum.StrEnum):
    pending = "pending"
    processing = "processing"
    completed = "completed"
    failed = "failed"
    partially_completed = "partially_completed"


class Record:
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    status: Mapped[Status] = mapped_column(
        Enum(Status, native_enum=False, create_constraint=True, name="processing_status"),
        default=Status.pending,
        server_default="pending",
        index=True,
    )


class Bid(Record, Base):
    __tablename__ = "bids"
    title: Mapped[str] = mapped_column(String(500))
    external_id: Mapped[str | None] = mapped_column(String(500), unique=True)


job_documents = Table(
    "job_documents",
    Base.metadata,
    Column("job_id", ForeignKey("processing_jobs.id", ondelete="CASCADE"), primary_key=True),
    Column("document_id", ForeignKey("documents.id", ondelete="RESTRICT"), primary_key=True),
)


class Document(Record, Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("object_key", name="uq_documents_object_key"),)
    bid_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("bids.id", ondelete="RESTRICT"), index=True
    )
    filename: Mapped[str] = mapped_column(String(500))
    object_key: Mapped[str] = mapped_column(String(1024))
    content_type: Mapped[str] = mapped_column(String(255))
    relative_path: Mapped[str | None] = mapped_column(String(1000))
    source_sha256: Mapped[str | None] = mapped_column(String(64))
    source_size: Mapped[int | None] = mapped_column(BigInteger)
    parsed_object_key: Mapped[str | None] = mapped_column(String(1024))
    parser_fingerprint: Mapped[str | None] = mapped_column(String(64))
    error_code: Mapped[str | None] = mapped_column(String(100))


class ProcessingJob(Record, Base):
    __tablename__ = "processing_jobs"
    __table_args__ = (Index("ix_processing_jobs_bid_status", "bid_id", "status"),)
    bid_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bids.id", ondelete="RESTRICT"))
    task_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    kind: Mapped[str] = mapped_column(String(100))
    error_code: Mapped[str | None] = mapped_column(String(100))
    parser_config: Mapped[dict | None] = mapped_column(JSON)
    documents: Mapped[list[Document]] = relationship(secondary=job_documents, lazy="selectin")
