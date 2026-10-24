"""Embedding cache pointers and per-generation search index state."""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "embedding_cache_entries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("profile_fingerprint", sa.String(64), nullable=False),
        sa.Column("artifact_key", sa.String(1024), nullable=False),
        sa.Column("artifact_sha256", sa.String(64), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("dense_dim", sa.Integer(), nullable=False),
        sa.Column("sparse_nnz", sa.Integer(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("truncated", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "content_hash", "profile_fingerprint", name="uq_embedding_cache_content_profile"
        ),
    )
    op.create_index(
        "ix_embedding_cache_entries_profile_fingerprint",
        "embedding_cache_entries",
        ["profile_fingerprint"],
    )
    op.create_table(
        "chunk_index_states",
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
        sa.Column(
            "generation_id",
            sa.Uuid(),
            sa.ForeignKey("chunk_generations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("bid_id", sa.Uuid(), sa.ForeignKey("bids.id"), nullable=False),
        sa.Column("document_id", sa.Uuid(), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("profile_fingerprint", sa.String(64), nullable=False),
        sa.Column("collection_name", sa.String(255), nullable=False),
        sa.Column("point_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cache_hits", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cache_misses", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("truncated_inputs", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(100)),
        sa.Column("timings", sa.JSON()),
        sa.UniqueConstraint(
            "generation_id", "profile_fingerprint", name="uq_chunk_index_generation_profile"
        ),
    )
    op.create_index("ix_chunk_index_states_status", "chunk_index_states", ["status"])
    op.create_index("ix_chunk_index_states_generation_id", "chunk_index_states", ["generation_id"])
    op.create_index(
        "ix_chunk_index_states_bid_profile",
        "chunk_index_states",
        ["bid_id", "profile_fingerprint"],
    )


def downgrade():
    op.drop_table("chunk_index_states")
    op.drop_table("embedding_cache_entries")
