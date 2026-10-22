"""Shops record their owner, so a teammate can't take over the owner's account.

- organisations.owner_id: the employee who created the shop. Staff admin actions (password,
  2-step reset, email, role, deactivate) on that account are refused for everyone but the
  owner. Backfilled with each shop's earliest-created employee, which is who sign-up made.

Revision ID: ow144q2
Revises: dp144q1
"""
import sqlalchemy as sa
from alembic import op

revision = "ow144q2"
down_revision = "dp144q1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("organisations") as b:
        b.add_column(sa.Column("owner_id", sa.String(), nullable=True))
    conn = op.get_bind()
    rows = conn.execute(sa.text(
        "SELECT org_id, id FROM employees WHERE org_id IS NOT NULL ORDER BY created_at ASC, id ASC")).fetchall()
    first = {}
    for org_id, emp_id in rows:
        first.setdefault(org_id, emp_id)
    for org_id, emp_id in first.items():
        conn.execute(sa.text("UPDATE organisations SET owner_id = :e WHERE id = :o AND owner_id IS NULL"),
                     {"e": emp_id, "o": org_id})


def downgrade() -> None:
    with op.batch_alter_table("organisations") as b:
        b.drop_column("owner_id")
