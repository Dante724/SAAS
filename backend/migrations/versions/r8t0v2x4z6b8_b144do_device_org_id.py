"""b144do (security) — devices get org_id for direct tenant scoping

Previously a device's tenant was inferred through client_id -> clients.org_id,
which left company-owned assets (client_id NULL) with no tenant anchor — a
cross-tenant IDOR on the assets endpoints. This adds devices.org_id and
backfills it: from the owning client where present, otherwise from the most
recent order that references the device. Rows that can't be resolved stay NULL
and are therefore unreachable cross-tenant (fail-closed).

Revision ID: r8t0v2x4z6b8
Revises: q7s9u1w3y5a7
Create Date: 2026-09-02
"""
from alembic import op
import sqlalchemy as sa

revision = "r8t0v2x4z6b8"
down_revision = "q7s9u1w3y5a7"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("devices") as b:
        b.add_column(sa.Column("org_id", sa.String(), nullable=True))
        b.create_index("ix_devices_org_id", ["org_id"])

    # Backfill from the owning client first (covers client-owned assets)…
    op.execute(
        "UPDATE devices SET org_id = ("
        "  SELECT c.org_id FROM clients c WHERE c.id = devices.client_id"
        ") WHERE client_id IS NOT NULL AND org_id IS NULL"
    )
    # …then from the most recent order referencing the device (covers
    # company-owned assets and any client-owned rows missed above).
    op.execute(
        "UPDATE devices SET org_id = ("
        "  SELECT o.org_id FROM orders o WHERE o.device_id = devices.id"
        "  ORDER BY o.created_at DESC LIMIT 1"
        ") WHERE org_id IS NULL"
    )


def downgrade():
    with op.batch_alter_table("devices") as b:
        b.drop_index("ix_devices_org_id")
        b.drop_column("org_id")
