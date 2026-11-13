"""A job line remembers the cost layers it drew: order_lines.stock_draws (INV-02, wave 4).

Giving a job's units back (line removed, qty lowered, job cancelled or archived, goods returned
against the job) refilled the newest layer with room at the location, not the layers the line drew,
so the shelf's valuation and the next sale's cost could shift. The line now keeps a journal of the
layers it drew, like sale_lines.stock_draws, and a restore refills those.

One nullable JSON column, no default, no backfill: a catalog-only change on Postgres (instant, no
table rewrite), a plain ADD COLUMN on SQLite (no table rebuild, so the indexes are untouched).
Lines drawn before this have no journal and keep restoring newest-first. The previous image never
reads or writes the column, so it can run against the upgraded schema during the deploy.

Downgrade drops the column (the journals are lost; restores fall back to newest-first).

Revision ID: w4i01
Revises: dbi03
"""
import sqlalchemy as sa
from alembic import op

revision = "w4i01"
down_revision = "dbi03"
branch_labels = None
depends_on = None


def _has_column(bind) -> bool:
    return "stock_draws" in {c["name"] for c in sa.inspect(bind).get_columns("order_lines")}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind):
        op.add_column("order_lines", sa.Column("stock_draws", sa.JSON(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind):
        # Postgres and SQLite >= 3.35 both drop a column in place (no index or constraint uses it)
        op.drop_column("order_lines", "stock_draws")
