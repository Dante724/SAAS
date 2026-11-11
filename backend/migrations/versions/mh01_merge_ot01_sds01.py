"""Merge the two migration heads ot01 (#107, operator text usage) and sds01 (#183, doc brand colour).

Both revised idg02 and merged on the same day, leaving two heads, so `alembic upgrade head`
refused to run. This revision only joins them: no schema change. It is safe whichever of the
two a database already has.

Revision ID: mh01
Revises: ot01, sds01
"""

revision = "mh01"
down_revision = ("ot01", "sds01")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
