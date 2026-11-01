"""Launch blocker C3 — purchase-order numbers are unique per shop, not platform-wide.

Numbering is per org (each shop's sequence starts at PO-00001), but the initial schema made
purchase_orders.po_number globally unique and the table had no org_id, so the second shop's
first PO hit a UniqueViolation. Same fix as orders in z6b8d0f2h4j6:

- add purchase_orders.org_id (FK + index), backfilled from the PO's location, else supplier;
- swap the global unique for UNIQUE(org_id, po_number).

Revision ID: ls144r2
Revises: ls144r1
"""
import sqlalchemy as sa
from alembic import op

revision = "ls144r2"
down_revision = "ls144r1"
branch_labels = None
depends_on = None

_NC = {"uq": "uq_%(table_name)s_%(column_0_name)s"}


def upgrade() -> None:
    with op.batch_alter_table("purchase_orders") as b:
        b.add_column(sa.Column("org_id", sa.String(), nullable=True))
        b.create_foreign_key("fk_purchase_orders_org_id_organisations", "organisations", ["org_id"], ["id"])
    op.create_index("ix_purchase_orders_org_id", "purchase_orders", ["org_id"])
    op.execute(
        "UPDATE purchase_orders SET org_id = COALESCE("
        "(SELECT org_id FROM locations WHERE locations.id = purchase_orders.location_id), "
        "(SELECT org_id FROM suppliers WHERE suppliers.id = purchase_orders.supplier_id)) "
        "WHERE org_id IS NULL")

    existing = {u["name"] for u in sa.inspect(op.get_bind()).get_unique_constraints("purchase_orders")}
    with op.batch_alter_table("purchase_orders", naming_convention=_NC) as b:
        for name in ("uq_purchase_orders_po_number", "purchase_orders_po_number_key"):
            if name in existing:
                b.drop_constraint(name, type_="unique")
                break
        b.create_unique_constraint("uq_purchase_orders_org_number", ["org_id", "po_number"])


def downgrade() -> None:
    with op.batch_alter_table("purchase_orders", naming_convention=_NC) as b:
        b.drop_constraint("uq_purchase_orders_org_number", type_="unique")
        b.create_unique_constraint("uq_purchase_orders_po_number", ["po_number"])
    op.drop_index("ix_purchase_orders_org_id", table_name="purchase_orders")
    with op.batch_alter_table("purchase_orders") as b:
        b.drop_constraint("fk_purchase_orders_org_id_organisations", type_="foreignkey")
        b.drop_column("org_id")
