"""Join the release/1.0 line and the parity line (with every open PR on both) into one head.

lr144p4 (legacy role permissions), mbk01 (memberships + parity join on release), sc144c1
(self check-in) and mpar01 (the parity PRs' join) touch different tables. Where both lines
added the same columns (platform admin 2-step, webhook retry), the parity migrations aud6m01
and aud3r01 are no-ops and the release migrations ad144s1 / whh1 add them. No schema change here.

Revision ID: mlaunch01
Revises: lr144p4, mbk01, mpar01, sc144c1
"""

revision = "mlaunch01"
down_revision = ("lr144p4", "mbk01", "mpar01", "sc144c1")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
