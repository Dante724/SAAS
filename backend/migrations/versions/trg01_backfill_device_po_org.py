"""Fill in org_id on devices and purchase orders that never got one.

The tenant read guard (backend/db/tenancy.py) shows a shop only rows whose org_id is that shop,
so a device or PO with a NULL org_id is visible to no shop at all. This fills the shop in wherever
the row's own links say which shop it is, in order of reliability:

- devices: the owning client's shop; else the location's; else the warehouse's; else the shop of
  the work orders that reference the device (only when they all belong to one shop).
- purchase orders: the location's shop (every PO has one); else the supplier's.

Rows whose shop can't be derived stay NULL (nothing is deleted). Making both columns NOT NULL is a
follow-up once production has been checked for leftovers. Downgrade changes nothing: the filled
values are correct, and there is no record of which rows were NULL before.

Revision ID: trg01
Revises: ce144w3
"""
import logging

from alembic import op

revision = "trg01"
down_revision = "ce144w3"
branch_labels = None
depends_on = None

log = logging.getLogger("alembic.trg01")

_DEVICE_SOURCES = [
    ("client", "SELECT c.org_id FROM clients c WHERE c.id = devices.client_id"),
    ("location", "SELECT l.org_id FROM locations l WHERE l.id = devices.location_id"),
    ("warehouse", "SELECT w.org_id FROM warehouses w WHERE w.id = devices.warehouse_id"),
    # A device on work orders of exactly one shop belongs to that shop.
    ("work order", "SELECT MIN(o.org_id) FROM orders o WHERE o.device_id = devices.id "
                   "HAVING COUNT(DISTINCT o.org_id) = 1"),
    ("order item", "SELECT MIN(oi.org_id) FROM order_items oi WHERE oi.asset_id = devices.id "
                   "HAVING COUNT(DISTINCT oi.org_id) = 1"),
]

_PO_SOURCES = [
    ("location", "SELECT l.org_id FROM locations l WHERE l.id = purchase_orders.location_id"),
    ("supplier", "SELECT s.org_id FROM suppliers s WHERE s.id = purchase_orders.supplier_id"),
]


def _fill(bind, table: str, sources) -> None:
    for name, sub in sources:
        res = bind.exec_driver_sql(
            f"UPDATE {table} SET org_id = ({sub}) WHERE org_id IS NULL AND ({sub}) IS NOT NULL")
        if res.rowcount:
            log.info("trg01: %s %s got their shop from the %s", res.rowcount, table, name)
    left = bind.exec_driver_sql(f"SELECT COUNT(*) FROM {table} WHERE org_id IS NULL").scalar()
    if left:
        log.warning("trg01: %s %s still have no shop (no client/location/supplier to tell)", left, table)


def upgrade() -> None:
    bind = op.get_bind()
    _fill(bind, "devices", _DEVICE_SOURCES)
    _fill(bind, "purchase_orders", _PO_SOURCES)


def downgrade() -> None:
    pass
