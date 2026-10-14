"""144jq — custom report builder: user-defined report definitions

Stores per-org report specs (source, columns, filters, group-by, aggregate)
validated against a strict server-side whitelist before running.

Revision ID: g2b4d6f8h0j2
Revises: f1a3c5e7g9i1
Create Date: 2026-09-20
"""
from alembic import op
import sqlalchemy as sa

revision = "g2b4d6f8h0j2"
down_revision = "f1a3c5e7g9i1"
branch_labels = None
depends_on = None


def _has_table(bind, table):
    insp = sa.inspect(bind)
    try:
        return table in insp.get_table_names()
    except Exception:
        return False


def upgrade():
    bind = op.get_bind()
    if _has_table(bind, "custom_reports"):
        return
    op.create_table(
        "custom_reports",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("org_id", sa.String(), sa.ForeignKey("organisations.id"), index=True, nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("created_by_id", sa.String(), sa.ForeignKey("employees.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )


def downgrade():
    bind = op.get_bind()
    if _has_table(bind, "custom_reports"):
        op.drop_table("custom_reports")
