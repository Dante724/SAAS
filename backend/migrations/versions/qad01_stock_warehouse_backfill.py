"""QA fix D — every location has a product warehouse; stock, layers and movements name theirs.

PO receipts and stock postings wrote stock rows, FIFO cost layers and movements with warehouse_id
NULL, while stock documents wrote the location's default warehouse — so one warehouse's stock showed
as two lines in valuation (the warehouse and a "—"). Locations added in Settings got no warehouse at
all until their first stock document. This backfill:

  1. gives every location without a live product warehouse its default one ("<location> warehouse");
  2. tags unbinned stock rows with the location's default product warehouse — merged into that
     warehouse's own unbinned row when it already has one (the unique index allows one per tag);
  3. tags bin rows and cost layers with the bin's warehouse, else the location's default;
  4. tags stock movements with the location's default.

Only NULL tags are touched, so running it again changes nothing. Downgrade is a no-op: once tagged,
a row can't be told apart from one written with its warehouse, and NULL only meant "the default".

Revision ID: qad01
Revises: ds144a1
"""
import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "qad01"
down_revision = "ds144a1"
branch_labels = None
depends_on = None

ACTIONS = ["post", "writeoff", "transfer", "sell", "stocktake"]

warehouses = sa.table(
    "warehouses", sa.column("id", sa.String), sa.column("org_id", sa.String), sa.column("name", sa.String),
    sa.column("kind", sa.String), sa.column("scope", sa.String), sa.column("location_id", sa.String),
    sa.column("is_default", sa.Boolean), sa.column("allowed_actions", sa.JSON), sa.column("active", sa.Boolean),
    sa.column("created_at", sa.DateTime))


def upgrade() -> None:
    conn = op.get_bind()

    # 1. a default product warehouse for every location that has none
    bare = conn.execute(sa.text(
        "SELECT l.id, l.org_id, l.name FROM locations l WHERE NOT EXISTS ("
        " SELECT 1 FROM warehouses w WHERE w.location_id = l.id AND w.kind = 'product'"
        " AND w.deleted_at IS NULL)")).all()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if bare:
        op.bulk_insert(warehouses, [
            {"id": str(uuid.uuid4()), "org_id": org_id, "name": f"{name or 'Location'} warehouse"[:120],
             "kind": "product", "scope": "location", "location_id": loc_id, "is_default": True,
             "allowed_actions": ACTIONS, "active": True, "created_at": now}
            for loc_id, org_id, name in bare])

    # the location's default: its is_default product warehouse, else its oldest one
    default: dict = {}
    for wid, loc_id in conn.execute(sa.text(
            "SELECT id, location_id FROM warehouses WHERE kind = 'product' AND deleted_at IS NULL"
            " AND location_id IS NOT NULL ORDER BY location_id, is_default DESC, created_at, id")).all():
        default.setdefault(loc_id, wid)

    # 2. unbinned stock rows
    for sid, part_id, loc_id, avail, res in conn.execute(sa.text(
            "SELECT id, part_id, location_id, qty_available, qty_reserved FROM stock_levels"
            " WHERE warehouse_id IS NULL AND bin_id IS NULL")).all():
        wid = default.get(loc_id)
        if not wid:
            continue
        own = conn.execute(sa.text(
            "SELECT id FROM stock_levels WHERE part_id = :p AND location_id = :l AND bin_id IS NULL"
            " AND warehouse_id = :w"), {"p": part_id, "l": loc_id, "w": wid}).scalar()
        if own:
            conn.execute(sa.text(
                "UPDATE stock_levels SET qty_available = COALESCE(qty_available, 0) + :a,"
                " qty_reserved = COALESCE(qty_reserved, 0) + :r WHERE id = :id"),
                {"a": avail or 0, "r": res or 0, "id": own})
            conn.execute(sa.text("DELETE FROM stock_levels WHERE id = :id"), {"id": sid})
        else:
            conn.execute(sa.text("UPDATE stock_levels SET warehouse_id = :w WHERE id = :id"),
                         {"w": wid, "id": sid})

    # 3. bin rows and cost layers: the bin's warehouse first, then the location's default
    for table in ("stock_levels", "stock_item_batches"):
        conn.execute(sa.text(
            f"UPDATE {table} SET warehouse_id = (SELECT sb.warehouse_id FROM storage_bins sb"
            f" WHERE sb.id = {table}.bin_id) WHERE warehouse_id IS NULL AND bin_id IS NOT NULL"))
    # 3b/4. whatever is still untagged belongs to the location's default
    for loc_id, wid in default.items():
        for table in ("stock_levels", "stock_item_batches", "stock_movements"):
            extra = " AND bin_id IS NOT NULL" if table == "stock_levels" else ""
            conn.execute(sa.text(
                f"UPDATE {table} SET warehouse_id = :w WHERE warehouse_id IS NULL AND location_id = :l{extra}"),
                {"w": wid, "l": loc_id})


def downgrade() -> None:
    pass
