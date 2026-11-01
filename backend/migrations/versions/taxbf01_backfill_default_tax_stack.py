"""QA 2026-10-31 B: existing shops charge their default tax on new jobs.

Shops whose job tax stack is empty (or holds only 0% / switched-off taxes) while they have an
active default tax above 0% get that tax as their stack. Shops that already stack a real tax are
untouched. Existing orders and invoices are not recomputed. Each change is logged (one line per
shop: id + tax name) and saved in tax_stack_backfill_log so downgrade can put the old stack back.
Logic and rules: backend/services/tax_backfill.py.

Revision ID: taxbf01
Revises: mdec01
"""
from alembic import op

revision = "taxbf01"
down_revision = "mdec01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from backend.services import tax_backfill
    conn = op.get_bind()
    tax_backfill.LOG.create(conn, checkfirst=True)
    tax_backfill.backfill(conn)


def downgrade() -> None:
    from backend.services import tax_backfill
    conn = op.get_bind()
    tax_backfill.restore(conn)
    tax_backfill.LOG.drop(conn, checkfirst=True)
