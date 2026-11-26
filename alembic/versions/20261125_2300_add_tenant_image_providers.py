"""Store image generation service settings per tenant."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "8b25imageprovider"
down_revision = "8b25productgallery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenant_image_providers",
        sa.Column("tenant_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column("api_format", sa.String(20), nullable=False),
        sa.Column("model_alias", sa.String(20), nullable=False),
        sa.Column("model_id", sa.String(200), nullable=False),
        sa.Column("encrypted_api_key", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
    )


def downgrade() -> None:
    op.drop_table("tenant_image_providers")
