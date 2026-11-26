"""Keep multiple image provider profiles and migrate the existing active connection."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "8b26providerprofiles"
down_revision = "8b25imageprovider"
branch_labels = None
depends_on = None


def upgrade() -> None:
    table = "tenant_image_providers"
    op.add_column(table, sa.Column("provider_id", UUID(as_uuid=True)))
    op.add_column(table, sa.Column("name", sa.String(80)))
    op.add_column(table, sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.execute(
        """
        UPDATE tenant_image_providers
        SET provider_id = gen_random_uuid(),
            name = COALESCE(NULLIF(left(split_part(split_part(base_url, '://', 2), '/', 1), 80), ''), '图片服务'),
            is_active = true
        """
    )
    op.alter_column(table, "name", nullable=False)
    op.drop_constraint("tenant_image_providers_pkey", table, type_="primary")
    op.create_primary_key("tenant_image_providers_pkey", table, ["provider_id"])
    op.create_index("idx_tenant_image_providers_tenant", table, ["tenant_id"])
    op.create_index(
        "uq_tenant_image_providers_active", table, ["tenant_id"], unique=True, postgresql_where=sa.text("is_active")
    )


def downgrade() -> None:
    table = "tenant_image_providers"
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM tenant_image_providers WHERE NOT is_active) THEN
                RAISE EXCEPTION 'Downgrade would lose saved image provider profiles or reactivate a paused profile';
            END IF;
        END $$;
        """
    )
    op.drop_index("uq_tenant_image_providers_active", table_name=table)
    op.drop_index("idx_tenant_image_providers_tenant", table_name=table)
    op.drop_constraint("tenant_image_providers_pkey", table, type_="primary")
    op.create_primary_key("tenant_image_providers_pkey", table, ["tenant_id"])
    op.drop_column(table, "is_active")
    op.drop_column(table, "name")
    op.drop_column(table, "provider_id")
