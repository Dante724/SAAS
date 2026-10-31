"""Preview: join the four branch heads into one so `alembic upgrade head` works.

- mkw01   roadmap/orderry-parity (kits + inquiries)
- lr144p4 roles: old-format permissions converted to codes
- mb145a1 memberships
- sc144c1 self check-in
They change different tables (the role conversion only rewrites old-format permission names;
the kits migration only adds new codes), so the order they run in doesn't matter.
No schema change here.

Revision ID: pv01
Revises: mkw01, lr144p4, mb145a1, sc144c1
"""

revision = "pv01"
down_revision = ("mkw01", "lr144p4", "mb145a1", "sc144c1")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
