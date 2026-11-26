"""Allow tenants to hide public products from their catalog."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "8b25hiddenproducts"
down_revision = "8a07agentcontext"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hidden_public_products",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", sa.String(160), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "document_id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], ["products.document_id"], ondelete="CASCADE"),
    )


def downgrade() -> None:
    op.drop_table("hidden_public_products")
