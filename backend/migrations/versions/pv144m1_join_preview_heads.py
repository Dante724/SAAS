"""Preview only: join the migration lines of every open PR so one database runs them all.

No schema change. When the PRs merge into main in order, each re-points onto the previous
head and this file is not needed.

Revision ID: pv144m1
Revises: ad144s2, ds144a1, gp144g4, ii144b01, sw144s3, wsb1, wsc1
"""
revision = "pv144m1"
down_revision = ("ad144s2", "ds144a1", "gp144g4", "ii144b01", "sw144s3", "wsb1", "wsc1")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
