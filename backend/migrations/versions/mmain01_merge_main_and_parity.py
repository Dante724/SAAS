"""Merge the two migration heads b9nan01 (main: clear stored non-finite values) and mh01
(roadmap/orderry-parity: idempotency, doc brand colour, operator text usage).

Both branches forked off trg01, so bringing parity into main left two heads and
`alembic upgrade head` refused to run. This revision only joins them: no schema change. It is
safe whichever of the two a database already has.

Revision ID: mmain01
Revises: b9nan01, mh01
"""

revision = "mmain01"
down_revision = ("b9nan01", "mh01")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
