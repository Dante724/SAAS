"""Build 144r — job_kits table (reusable parts+labour bundles).

Revision ID: kit144r0002
Revises: dep144q0001
"""
import sqlalchemy as sa
from alembic import op

revision = "kit144r0002"
down_revision = "dep144q0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "job_kits",
        sa.Column("id", sa.String(), primary_key=True),
        # NB: no index=True here — the explicit op.create_index below owns ix_job_kits_org_id.
        # Having both made create_table auto-create the index, then the explicit create duplicated
        # it → "index already exists" aborted `alembic upgrade head` from scratch (prod deploys).
        sa.Column("org_id", sa.String(), sa.ForeignKey("organisations.id"), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("lines", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_job_kits_org_id", "job_kits", ["org_id"])
    op.create_index("ix_job_kits_active", "job_kits", ["active"])


def downgrade() -> None:
    op.drop_index("ix_job_kits_active", table_name="job_kits")
    op.drop_index("ix_job_kits_org_id", table_name="job_kits")
    op.drop_table("job_kits")
