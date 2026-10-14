"""Build 144ej — order_number is unique PER ORG, not globally.

The initial schema made orders.order_number globally unique, but numbering is
per-org (each org's sequence starts at WO-00001), so a second tenant's first order
collided. Orderry scopes numbering to the account; this matches that: swap the
global unique constraint for a composite unique on (org_id, order_number).

Revision ID: z6b8d0f2h4j6
Revises: y5a7c9e1g3i5
"""
from alembic import op

revision = "z6b8d0f2h4j6"
down_revision = "y5a7c9e1g3i5"
branch_labels = None
depends_on = None

# lets Alembic compute the name of the original (unnamed) column-level unique so it
# can be dropped on SQLite as well as named-constraint databases.
_NC = {"uq": "uq_%(table_name)s_%(column_0_name)s"}


def upgrade() -> None:
    with op.batch_alter_table("orders", naming_convention=_NC) as b:
        for name in ("uq_orders_order_number", "orders_order_number_key"):
            try:
                b.drop_constraint(name, type_="unique")
                break
            except Exception:
                continue
        b.create_unique_constraint("uq_order_org_number", ["org_id", "order_number"])


def downgrade() -> None:
    with op.batch_alter_table("orders", naming_convention=_NC) as b:
        b.drop_constraint("uq_order_org_number", type_="unique")
        b.create_unique_constraint("uq_orders_order_number", ["order_number"])
