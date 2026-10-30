"""Invoices remember when they were sent (invoices.sent_at).

A sent invoice is locked: it keeps the job lines it billed instead of following later job
changes, and moves from draft to issued. Invoices already marked issued or overdue count as
sent (sent_at = issued_at); part-paid ones can't be told apart and behave as unsent until paid
or sent again.

Also links every invoice to its primary job (invoice_orders), which older paths skipped.

Also: invoice discount and tax now always mean amounts (total = subtotal − discount + tax).
Invoices made in the manual editor before stored them as percentages. Those are found by their
total (it only adds up the percentage way) and rewritten to amounts; the total doesn't change.

Revision ID: im01
Revises: mkw01
"""
import sqlalchemy as sa
from alembic import op

revision = "im01"
down_revision = "mkw01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("invoices")}
    if "sent_at" not in cols:
        op.add_column("invoices", sa.Column("sent_at", sa.DateTime(), nullable=True))
    op.execute("UPDATE invoices SET sent_at = COALESCE(issued_at, created_at) "
               "WHERE sent_at IS NULL AND status IN ('issued', 'overdue')")
    _percent_to_amounts(op.get_bind())
    # Invoices made by the old bulk / status-change / sub-invoice paths had no job link row, so
    # attaching a second job made them forget their first one. Link every primary job.
    op.execute("INSERT INTO invoice_orders (id, invoice_id, order_id, sort_order) "
               "SELECT i.id || '-io', i.id, i.order_id, 0 FROM invoices i WHERE i.order_id IS NOT NULL "
               "AND NOT EXISTS (SELECT 1 FROM invoice_orders l WHERE l.invoice_id = i.id AND l.order_id = i.order_id)")


def _q2(x: float) -> float:
    from decimal import ROUND_HALF_UP, Decimal
    return float(Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _percent_to_amounts(bind) -> None:
    rows = bind.execute(sa.text(
        "SELECT id, subtotal, discount, tax, total FROM invoices WHERE discount > 0 OR tax > 0")).all()
    for iid, sub, disc, tax, total in rows:
        sub, disc, tax, total = (float(v or 0) for v in (sub, disc, tax, total))
        if abs(_q2(sub - disc + tax) - total) < 0.005 or disc > 100:
            continue                                   # already amounts
        if abs(_q2(sub * (1 - disc / 100) * (1 + tax / 100)) - total) >= 0.005:
            continue                                   # neither reading fits: leave it for a person
        d = _q2(sub * disc / 100)
        t = _q2(total - (sub - d))
        bind.execute(sa.text("UPDATE invoices SET discount = :d, tax = :t WHERE id = :i"),
                     {"d": d, "t": t, "i": iid})


def downgrade() -> None:
    with op.batch_alter_table("invoices") as b:
        b.drop_column("sent_at")
