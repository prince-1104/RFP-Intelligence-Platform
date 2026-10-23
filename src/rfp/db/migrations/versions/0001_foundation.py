"""Initial infrastructure tables."""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def common_columns():
    return [
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
                native_enum=False,
                create_constraint=True,
                name="processing_status",
            ),
            nullable=False,
            server_default="pending",
        ),
    ]


def upgrade():
    op.create_table("bids", *common_columns(), sa.Column("title", sa.String(500), nullable=False))
    op.create_table(
        "documents",
        *common_columns(),
        sa.Column(
            "bid_id", sa.Uuid(), sa.ForeignKey("bids.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("filename", sa.String(500), nullable=False),
        sa.Column("object_key", sa.String(1024), nullable=False),
        sa.Column("content_type", sa.String(255), nullable=False),
        sa.UniqueConstraint("object_key", name="uq_documents_object_key"),
    )
    op.create_table(
        "processing_jobs",
        *common_columns(),
        sa.Column(
            "bid_id", sa.Uuid(), sa.ForeignKey("bids.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("task_id", sa.String(255), unique=True),
        sa.Column("kind", sa.String(100), nullable=False),
        sa.Column("error_code", sa.String(100)),
    )
    for table in ("bids", "documents", "processing_jobs"):
        op.create_index(f"ix_{table}_status", table, ["status"])
    op.create_index("ix_documents_bid_id", "documents", ["bid_id"])
    op.create_index("ix_processing_jobs_bid_status", "processing_jobs", ["bid_id", "status"])


def downgrade():
    for table in ("processing_jobs", "documents", "bids"):
        op.drop_table(table)
