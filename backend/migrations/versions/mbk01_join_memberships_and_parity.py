"""Join memberships (mb145a1) and the release→parity sync (syp01) into one head.

Memberships adds its own tables plus membership columns on order_lines; the parity side adds
kit columns on order_lines and kit permissions. Different columns, so order doesn't matter.
No schema change here.

Revision ID: mbk01
Revises: mb145a1, syp01
"""

revision = "mbk01"
down_revision = ("mb145a1", "syp01")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
