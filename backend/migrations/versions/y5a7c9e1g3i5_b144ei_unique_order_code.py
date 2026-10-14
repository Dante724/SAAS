"""Build 144ei — globally-unique work-order code.

Every work order gets a scannable code that is unique across the WHOLE system
(all tenants), never repeated — Orderry semantics. Backfills a unique numeric code
for any order missing one (or sharing one), then adds a DB-wide unique index on
orders.barcode so a duplicate can never be stored again.

Revision ID: y5a7c9e1g3i5
Revises: x4z6b8d0f2h4
"""
import secrets

from alembic import op
import sqlalchemy as sa

revision = "y5a7c9e1g3i5"
down_revision = "x4z6b8d0f2h4"
branch_labels = None
depends_on = None


def _code() -> str:
    return "2" + "".join(secrets.choice("0123456789") for _ in range(11))


def upgrade() -> None:
    bind = op.get_bind()
    rows = list(bind.execute(sa.text("SELECT id, barcode FROM orders")))
    used = {r[1] for r in rows if r[1]}
    # any order with no code, or a code that collides with another order, gets a
    # fresh globally-unique one so the unique index can be created cleanly.
    seen = set()
    for oid, code in rows:
        if code and code not in seen:
            seen.add(code)
            continue
        # missing or duplicate -> assign a fresh unique code
        c = _code()
        while c in used:
            c = _code()
        used.add(c); seen.add(c)
        bind.execute(sa.text("UPDATE orders SET barcode = :c WHERE id = :i"), {"c": c, "i": oid})
    op.create_index("uq_orders_barcode", "orders", ["barcode"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_orders_barcode", table_name="orders")
