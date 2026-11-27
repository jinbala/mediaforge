"""Persist Amazon research runs, checkpoints and report snapshots."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision = "8b28amazonresearch"
down_revision = "8b26providerprofiles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    table = "amazon_research_runs"
    op.create_table(
        table,
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.tenant_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("canonical_source", sa.Text(), nullable=False),
        sa.Column("active_key", sa.String(64)),
        sa.Column("submission_id", UUID(as_uuid=True)),
        sa.Column("title", sa.Text()),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("stage", sa.String(20), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("lease_token", UUID(as_uuid=True)),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("inputs", sa.JSON(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("report", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_amazon_run_owner_time", table, ["tenant_id", "user_id", "created_at"])
    op.create_index(
        "uq_amazon_run_active", table, ["tenant_id", "user_id", "active_key"], unique=True
    )
    op.create_index(
        "uq_amazon_run_submission", table, ["tenant_id", "user_id", "submission_id"], unique=True
    )


def downgrade() -> None:
    op.drop_table("amazon_research_runs")
