"""Source provenance, artifact references and ingestion job membership."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

OLD = "'pending', 'processing', 'completed', 'failed'"


def upgrade():
    for table in ("bids", "documents", "processing_jobs"):
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint("processing_status", type_="check")
            batch.alter_column(
                "status", type_=sa.String(19), existing_type=sa.String(10), existing_nullable=False
            )
            batch.create_check_constraint(
                "processing_status", f"status IN ({OLD}, 'partially_completed')"
            )
    with op.batch_alter_table("bids") as batch:
        batch.add_column(sa.Column("external_id", sa.String(500)))
        batch.create_unique_constraint("uq_bids_external_id", ["external_id"])
    with op.batch_alter_table("documents") as batch:
        columns: list[tuple[str, sa.types.TypeEngine]] = [
            ("relative_path", sa.String(1000)),
            ("source_sha256", sa.String(64)),
            ("source_size", sa.BigInteger()),
            ("parsed_object_key", sa.String(1024)),
            ("parser_fingerprint", sa.String(64)),
            ("error_code", sa.String(100)),
        ]
        for name, kind in columns:
            batch.add_column(sa.Column(name, kind))
    with op.batch_alter_table("processing_jobs") as batch:
        batch.add_column(sa.Column("parser_config", sa.JSON()))
    op.create_table(
        "job_documents",
        sa.Column(
            "job_id",
            sa.Uuid(),
            sa.ForeignKey("processing_jobs.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "document_id",
            sa.Uuid(),
            sa.ForeignKey("documents.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
    )


def downgrade():
    op.drop_table("job_documents")
    with op.batch_alter_table("processing_jobs") as batch:
        batch.drop_column("parser_config")
    with op.batch_alter_table("documents") as batch:
        for name in (
            "error_code",
            "parser_fingerprint",
            "parsed_object_key",
            "source_size",
            "source_sha256",
            "relative_path",
        ):
            batch.drop_column(name)
    with op.batch_alter_table("bids") as batch:
        batch.drop_constraint("uq_bids_external_id", type_="unique")
        batch.drop_column("external_id")
    for table in ("bids", "documents", "processing_jobs"):
        op.execute(
            sa.text(f"UPDATE {table} SET status='failed' WHERE status='partially_completed'")
        )
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint("processing_status", type_="check")
            batch.alter_column(
                "status", type_=sa.String(10), existing_type=sa.String(19), existing_nullable=False
            )
            batch.create_check_constraint("processing_status", f"status IN ({OLD})")
