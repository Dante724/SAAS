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



def _indexes(table: str) -> set:
    """Index names that exist right now. Checking first (instead of try/except around DDL) matters
    on Postgres: a failed statement aborts the whole migration transaction, so the except can't
    recover and the upgrade fails later with a confusing error."""
    import sqlalchemy as _sa
    insp = _sa.inspect(op.get_bind())
    if not insp.has_table(table):
        return set()
    return {i["name"] for i in insp.get_indexes(table)}

def upgrade() -> None:
    existing = _indexes("job_kits")
    for ix in ("ix_job_kits_active", "ix_job_kits_org_id"):
        if ix in existing:   # already gone on some DBs
            op.drop_index(ix, table_name="job_kits")
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
