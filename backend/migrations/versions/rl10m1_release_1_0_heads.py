"""release/1.0: join the preview's merge head with the API v2 line (bk1, PR #29).

No schema change.

Revision ID: rl10m1
Revises: pv144m1, bk1
"""
revision = "rl10m1"
down_revision = ("pv144m1", "bk1")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
