"""Canonical chunk text, source occurrences and transactional generation activation."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("documents", sa.Column("parsed_artifact_sha256", sa.String(64)))
    op.create_index("ix_documents_bid_source_hash", "documents", ["bid_id", "source_sha256"])
    op.create_table(
        "chunk_contents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("content_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_table(
        "chunk_generations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "processing",
                "completed",
                "failed",
                "partially_completed",
                native_enum=False,
                create_constraint=True,
                name="processing_status",
            ),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("document_id", sa.Uuid(), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("bid_id", sa.Uuid(), sa.ForeignKey("bids.id"), nullable=False),
        sa.Column("source_path", sa.String(1000), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("parsed_object_key", sa.String(1024), nullable=False),
        sa.Column("config_hash", sa.String(64), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("artifact_key", sa.String(1024)),
        sa.Column("artifact_hash", sa.String(64)),
        sa.Column("normalized_content_hash", sa.String(64)),
        sa.Column("schema_version", sa.String(20), nullable=False, server_default="1.0"),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reused_content_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(100)),
        sa.UniqueConstraint(
            "document_id", "input_hash", "config_hash", name="uq_chunk_generation_input"
        ),
    )
    for field in ("document_id", "bid_id", "config_hash", "status"):
        op.create_index(f"ix_chunk_generations_{field}", "chunk_generations", [field])
    op.create_index(
        "uq_chunk_generation_active_source",
        "chunk_generations",
        ["bid_id", "source_path"],
        unique=True,
        postgresql_where=sa.text("active"),
        sqlite_where=sa.text("active = 1"),
    )
    op.create_table(
        "document_chunks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "generation_id",
            sa.Uuid(),
            sa.ForeignKey("chunk_generations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("document_id", sa.Uuid(), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("content_id", sa.Uuid(), sa.ForeignKey("chunk_contents.id"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("provenance", sa.JSON(), nullable=False),
        sa.UniqueConstraint("generation_id", "sequence", name="uq_document_chunk_sequence"),
    )
    for field in ("generation_id", "document_id", "content_id"):
        op.create_index(f"ix_document_chunks_{field}", "document_chunks", [field])
    op.create_table(
        "job_chunk_generations",
        sa.Column(
            "job_id",
            sa.Uuid(),
            sa.ForeignKey("processing_jobs.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "generation_id",
            sa.Uuid(),
            sa.ForeignKey("chunk_generations.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
    )


def downgrade():
    for table in (
        "job_chunk_generations",
        "document_chunks",
        "chunk_generations",
        "chunk_contents",
    ):
        op.drop_table(table)
    op.drop_index("ix_documents_bid_source_hash", table_name="documents")
    with op.batch_alter_table("documents") as batch:
        batch.drop_column("parsed_artifact_sha256")
