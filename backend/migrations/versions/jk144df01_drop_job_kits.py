"""Build 144df — drop the legacy job_kits table (the JSON JobKit was retired; the Bundle engine
is the single job-kit concept now, relabelled "Job Kit" in the UI in 144cx).

Revision ID: jk144df01
Revises: dv144db01
"""
import sqlalchemy as sa
from alembic import op

revision = "jk144df01"
down_revision = "dv144db01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for ix in ("ix_job_kits_active", "ix_job_kits_org_id"):
        try:
            op.drop_index(ix, table_name="job_kits")
        except Exception:  # noqa: BLE001 — already gone on some DBs
            pass
    op.drop_table("job_kits")


def downgrade() -> None:
    op.create_table(
        "job_kits",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("org_id", sa.String(), sa.ForeignKey("organisations.id")),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("active", sa.Boolean(), default=True),
        sa.Column("lines", sa.JSON()),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime()),
    )
    op.create_index("ix_job_kits_org_id", "job_kits", ["org_id"])
    op.create_index("ix_job_kits_active", "job_kits", ["active"])
