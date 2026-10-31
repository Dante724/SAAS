"""Launch audit — missing indexes and two uniqueness guarantees.

Indexes on the org / foreign-key columns large tables are filtered by (event feed, returns,
feedback, payroll ledger, PO lines, sales, devices, payment intents, stock movements).

Uniques, each skipped (with the clashing values printed) if legacy rows already break it, so a
deploy never fails on old data; the app's numbering already avoids new clashes:
- invoices (org_id, invoice_number): one invoice number per shop
  (scripts/find_duplicate_invoice_numbers.py lists clashes to fix first);
- payment_intents.payment_id: one recorded payment settles at most one pay link.

Revision ID: ls144r4
Revises: ls144r3
"""
import sqlalchemy as sa
from alembic import op

revision = "ls144r4"
down_revision = "ls144r3"
branch_labels = None
depends_on = None

INDEXES = [
    ("event_feed_items", "org_id"), ("client_returns", "org_id"), ("client_returns", "order_id"),
    ("client_returns", "client_id"), ("feedback", "org_id"), ("feedback", "order_id"),
    ("employee_balance_ledger", "org_id"), ("work_order_types", "org_id"), ("po_lines", "po_id"),
    ("sales", "client_id"), ("devices", "client_id"), ("payment_intents", "invoice_id"),
    ("stock_movements", "location_id"),
]


def _clashes(sql: str) -> list:
    return [r[0] for r in op.get_bind().execute(sa.text(sql)).fetchall()]


def upgrade() -> None:
    for table, col in INDEXES:
        op.create_index(f"ix_{table}_{col}", table, [col])

    dup_inv = _clashes("SELECT invoice_number FROM invoices WHERE invoice_number IS NOT NULL "
                       "GROUP BY org_id, invoice_number HAVING count(*) > 1")
    if dup_inv:
        print("ls144r4: NOT adding uq_invoices_org_number; duplicate invoice numbers: "
              + ", ".join(sorted(set(dup_inv))[:20]) + ". Run scripts/find_duplicate_invoice_numbers.py.")
    else:
        with op.batch_alter_table("invoices") as b:
            b.create_unique_constraint("uq_invoices_org_number", ["org_id", "invoice_number"])

    dup_pay = _clashes("SELECT payment_id FROM payment_intents WHERE payment_id IS NOT NULL "
                       "GROUP BY payment_id HAVING count(*) > 1")
    if dup_pay:
        print("ls144r4: payment_intents.payment_id has duplicates, indexing without UNIQUE: "
              + ", ".join(dup_pay[:20]))
        op.create_index("ix_payment_intents_payment_id", "payment_intents", ["payment_id"])
    else:
        op.create_index("ix_payment_intents_payment_id", "payment_intents", ["payment_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_payment_intents_payment_id", table_name="payment_intents")
    insp = sa.inspect(op.get_bind())
    if "uq_invoices_org_number" in {u["name"] for u in insp.get_unique_constraints("invoices")}:
        with op.batch_alter_table("invoices") as b:
            b.drop_constraint("uq_invoices_org_number", type_="unique")
    for table, col in INDEXES:
        op.drop_index(f"ix_{table}_{col}", table_name=table)
