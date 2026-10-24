import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
    text,
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
    __table_args__ = (
        UniqueConstraint("object_key", name="uq_documents_object_key"),
        Index("ix_documents_bid_source_hash", "bid_id", "source_sha256"),
    )
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
    parsed_artifact_sha256: Mapped[str | None] = mapped_column(String(64))
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
    chunk_generations: Mapped[list["ChunkGeneration"]] = relationship(
        secondary="job_chunk_generations", lazy="selectin"
    )


job_chunk_generations = Table(
    "job_chunk_generations",
    Base.metadata,
    Column("job_id", ForeignKey("processing_jobs.id", ondelete="CASCADE"), primary_key=True),
    Column(
        "generation_id", ForeignKey("chunk_generations.id", ondelete="RESTRICT"), primary_key=True
    ),
)


class ChunkContent(Base):
    __tablename__ = "chunk_contents"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    content_hash: Mapped[str] = mapped_column(String(64), unique=True)
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ChunkGeneration(Record, Base):
    __tablename__ = "chunk_generations"
    __table_args__ = (
        UniqueConstraint(
            "document_id", "input_hash", "config_hash", name="uq_chunk_generation_input"
        ),
        Index(
            "uq_chunk_generation_active_source",
            "bid_id",
            "source_path",
            unique=True,
            postgresql_where=text("active"),
            sqlite_where=text("active = 1"),
        ),
    )
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"), index=True)
    bid_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bids.id"), index=True)
    source_path: Mapped[str] = mapped_column(String(1000))
    input_hash: Mapped[str] = mapped_column(String(64))
    parsed_object_key: Mapped[str] = mapped_column(String(1024))
    config_hash: Mapped[str] = mapped_column(String(64), index=True)
    config: Mapped[dict] = mapped_column(JSON)
    active: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    artifact_key: Mapped[str | None] = mapped_column(String(1024))
    artifact_hash: Mapped[str | None] = mapped_column(String(64))
    normalized_content_hash: Mapped[str | None] = mapped_column(String(64))
    schema_version: Mapped[str] = mapped_column(String(20), default="1.0", server_default="1.0")
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    reused_content_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    error_code: Mapped[str | None] = mapped_column(String(100))


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint("generation_id", "sequence", name="uq_document_chunk_sequence"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    generation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chunk_generations.id", ondelete="CASCADE"), index=True
    )
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"), index=True)
    content_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chunk_contents.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    provenance: Mapped[dict] = mapped_column(JSON)


class EmbeddingCacheEntry(Base):
    """Pointer to a vector inside an immutable R2 batch artifact; vectors are not stored here."""

    __tablename__ = "embedding_cache_entries"
    __table_args__ = (
        UniqueConstraint(
            "content_hash", "profile_fingerprint", name="uq_embedding_cache_content_profile"
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    profile_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    artifact_key: Mapped[str] = mapped_column(String(1024))
    artifact_sha256: Mapped[str] = mapped_column(String(64))
    position: Mapped[int] = mapped_column(Integer)
    dense_dim: Mapped[int] = mapped_column(Integer)
    sparse_nnz: Mapped[int] = mapped_column(Integer)
    token_count: Mapped[int] = mapped_column(Integer)
    truncated: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ChunkIndexState(Record, Base):
    __tablename__ = "chunk_index_states"
    __table_args__ = (
        UniqueConstraint(
            "generation_id", "profile_fingerprint", name="uq_chunk_index_generation_profile"
        ),
        Index("ix_chunk_index_states_bid_profile", "bid_id", "profile_fingerprint"),
    )
    generation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chunk_generations.id", ondelete="RESTRICT"), index=True
    )
    bid_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bids.id"))
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"))
    profile_fingerprint: Mapped[str] = mapped_column(String(64))
    collection_name: Mapped[str] = mapped_column(String(255))
    point_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    cache_hits: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    cache_misses: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    truncated_inputs: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    error_code: Mapped[str | None] = mapped_column(String(100))
    timings: Mapped[dict | None] = mapped_column(JSON)
