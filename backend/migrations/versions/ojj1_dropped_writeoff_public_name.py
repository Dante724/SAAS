"""Orderry Jun–Jul 2026 fixes.

- orders.written_off: the unpaid balance written off when a job moves to a dropped status, so a
  dropped job no longer shows as owed. Reversed when the job is reopened.
- organisations.public_employee_name (full | first | custom): how staff names appear on the
  booking page and in customer messages.

Revision ID: ojj1
Revises: opd1
"""
import sqlalchemy as sa
from alembic import op

revision = "ojj1"
down_revision = "opd1"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    return column in {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    # wsb1 (a sibling off opd1) adds the same column; whichever runs first adds it.
    if not _has_column("orders", "written_off"):
        with op.batch_alter_table("orders") as b:
            b.add_column(sa.Column("written_off", sa.Numeric(12, 2, asdecimal=False), nullable=False,
                                   server_default="0"))
    with op.batch_alter_table("organisations") as b:
        b.add_column(sa.Column("public_employee_name", sa.String(length=10), nullable=False,
                               server_default="full"))


def downgrade() -> None:
    with op.batch_alter_table("organisations") as b:
        b.drop_column("public_employee_name")
    if _has_column("orders", "written_off"):
        with op.batch_alter_table("orders") as b:
            b.drop_column("written_off")
