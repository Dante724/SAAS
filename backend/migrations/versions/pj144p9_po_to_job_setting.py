"""Settings → Inventory: parts arriving on a job's PO are written off to the job
automatically (default) or left for staff to do by hand (ADR-144po).

Revision ID: pj144p9
Revises: rp144p8
"""
import sqlalchemy as sa
from alembic import op

revision = "pj144p9"
down_revision = "rp144p8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("organisations") as b:
        b.add_column(sa.Column("inv_po_to_job", sa.String(10), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("organisations") as b:
        b.drop_column("inv_po_to_job")
