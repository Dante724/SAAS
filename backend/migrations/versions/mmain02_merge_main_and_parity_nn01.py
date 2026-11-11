"""Merge the two migration heads mmain01 (main + parity join) and nn01 (parity: org_id NOT NULL
on the tenant tables).

nn01 landed on roadmap/orderry-parity on top of mh01 after mmain01 joined mh01 to main's
b9nan01, leaving two heads again. This revision only joins them: no schema change. It is safe
whichever of the two a database already has.

Revision ID: mmain02
Revises: mmain01, nn01
"""

revision = "mmain02"
down_revision = ("mmain01", "nn01")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
