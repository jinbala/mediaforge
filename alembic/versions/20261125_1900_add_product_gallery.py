"""Store additional product gallery photos in source order."""

import sqlalchemy as sa
from alembic import op

revision = "8b25productgallery"
down_revision = "8b25hiddenproducts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column("gallery_image_urls", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )


def downgrade() -> None:
    op.drop_column("products", "gallery_image_urls")
