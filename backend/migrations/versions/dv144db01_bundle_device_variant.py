"""Build 144db — bundle_components.device_variant (device-aware job kits).

A product component flagged device_variant resolves, at apply-to-WO time, to the in-category part
that fits the order's device; the stored ref_id is the fallback.

Revision ID: dv144db01
Revises: pt144cu01
"""
import sqlalchemy as sa
from alembic import op

revision = "dv144db01"
down_revision = "pt144cu01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("bundle_components") as batch:
        batch.add_column(sa.Column("device_variant", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("bundle_components") as batch:
        batch.drop_column("device_variant")
