import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Status(enum.StrEnum):
    pending = "pending"
    processing = "processing"
    completed = "completed"
    failed = "failed"


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


class Document(Record, Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("object_key", name="uq_documents_object_key"),)
    bid_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("bids.id", ondelete="RESTRICT"), index=True
    )
    filename: Mapped[str] = mapped_column(String(500))
    object_key: Mapped[str] = mapped_column(String(1024))
    content_type: Mapped[str] = mapped_column(String(255))


class ProcessingJob(Record, Base):
    __tablename__ = "processing_jobs"
    __table_args__ = (Index("ix_processing_jobs_bid_status", "bid_id", "status"),)
    bid_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bids.id", ondelete="RESTRICT"))
    task_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    kind: Mapped[str] = mapped_column(String(100))
    error_code: Mapped[str | None] = mapped_column(String(100))
