"""144ay — append-only time-entry edit audit log (ADR 144ax → Option B).

Revision ID: mtl144ay001
Revises: mtl144ax001
Create Date: 2026-10-01
"""
from alembic import op
import sqlalchemy as sa

revision = "mtl144ay001"
down_revision = "mtl144ax001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "time_entry_audits",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("org_id", sa.String(), sa.ForeignKey("organisations.id"), index=True),
        sa.Column("time_entry_id", sa.String(), sa.ForeignKey("time_entries.id"), index=True),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("field", sa.String(length=32)),
        sa.Column("old_value", sa.Text()),
        sa.Column("new_value", sa.Text()),
        sa.Column("actor_id", sa.String(), sa.ForeignKey("employees.id")),
        sa.Column("at", sa.DateTime(), index=True),
    )


def downgrade():
    op.drop_table("time_entry_audits")
