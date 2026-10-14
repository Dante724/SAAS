"""b144ca — per-user UI preferences (server-side saved views)

Revision ID: i9k1m3o5q7s9
Revises: h8j0l2n4p6r8
Create Date: 2026-08-20
"""
from alembic import op
import sqlalchemy as sa

revision = "i9k1m3o5q7s9"
down_revision = "h8j0l2n4p6r8"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "user_prefs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("org_id", sa.String(), sa.ForeignKey("organisations.id"), nullable=False),
        sa.Column("employee_id", sa.String(), sa.ForeignKey("employees.id"), nullable=False, index=True),
        sa.Column("pref_key", sa.String(length=120), nullable=False),
        sa.Column("value", sa.JSON(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("uq_userpref_emp_key", "user_prefs", ["employee_id", "pref_key"], unique=True)


def downgrade():
    op.drop_index("uq_userpref_emp_key", table_name="user_prefs")
    op.drop_table("user_prefs")
