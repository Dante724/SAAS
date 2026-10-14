"""144ex: tasks.assignee_ids — multiple technicians per task (additive)

Revision ID: e1g3i5k7m9o1
Revises: d0f2h4j6l8n0
Create Date: 2026-09-16
"""
from alembic import op
import sqlalchemy as sa

revision = "e1g3i5k7m9o1"
down_revision = "d0f2h4j6l8n0"
branch_labels = None
depends_on = None


def upgrade():
    # nullable JSON list of employee ids; assigned_to_id stays as the primary owner
    op.add_column("tasks", sa.Column("assignee_ids", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("tasks", "assignee_ids")
