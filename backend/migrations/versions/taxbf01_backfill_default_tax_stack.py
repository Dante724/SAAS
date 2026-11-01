"""QA 2026-10-31 B: existing shops charge their default tax on new jobs.

Shops whose job tax stack is empty (or holds only 0% / switched-off taxes) while they have an
active default tax above 0% get that tax as their stack. Shops that already stack a real tax are
untouched. Existing orders and invoices are not recomputed. Each change is logged (one line per
shop: id + tax name) and saved in tax_stack_backfill_log so downgrade can put the old stack back.
Logic and rules: backend/services/tax_backfill.py.

Revision ID: taxbf01
Revises: rt144a1
"""
import sqlalchemy as sa
from alembic import op

revision = "taxbf01"
down_revision = "rt144a1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from backend.services import tax_backfill
    op.create_table(
        "tax_stack_backfill_log",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("org_id", sa.String(), nullable=False),
        sa.Column("previous_tax_ids", sa.Text(), nullable=False),
        sa.Column("tax_id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_tax_stack_backfill_log_org_id", "tax_stack_backfill_log", ["org_id"])
    tax_backfill.backfill(op.get_bind())


def downgrade() -> None:
    from backend.services import tax_backfill
    tax_backfill.restore(op.get_bind())
    op.drop_index("ix_tax_stack_backfill_log_org_id", table_name="tax_stack_backfill_log")
    op.drop_table("tax_stack_backfill_log")
