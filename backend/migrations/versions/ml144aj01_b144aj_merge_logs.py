"""Build 144aj — merge_logs table makes a contact merge reversible.

Each merge writes one row: the deleted sources' snapshots, a map of which records moved
(and from which source), and the target's prior fields/balance — everything undo needs.
Dev (SQLite) creates this table via create_all; this migration owns it for production.

Revision ID: b8d0f2h4j6k8
Revises: a7c9e1g3i5k7
"""
import sqlalchemy as sa
from alembic import op

revision = "ml144aj01"
down_revision = "wa144ag01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "merge_logs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("org_id", sa.String(), sa.ForeignKey("organisations.id"), index=True),
        sa.Column("target_id", sa.String(), index=True),
        sa.Column("source_snapshots", sa.JSON(), nullable=True),
        sa.Column("moved", sa.JSON(), nullable=True),
        sa.Column("target_before", sa.JSON(), nullable=True),
        sa.Column("created_by_id", sa.String(), sa.ForeignKey("employees.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("undone_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("merge_logs")
