"""QA fix D (31 Oct 2026) — stock cost & valuation.

Every test ends on the same two books checks: stock movements add up to what is on hand, and the
FIFO cost layers hold exactly the on-hand units (so COGS and valuation are drawn from real costs).
"""
from pathlib import Path

import pytest
from sqlalchemy import func, select

from backend.models import Part, StockItemBatch, StockLevel, StockMovement


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


async def _part(client, h, sku, **extra):
    body = {"name": f"Part {sku}", "sku": sku, **extra}
    r = await client.post("/api/inventory/parts", json=body, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _get(client, h, pid):
    return (await client.get(f"/api/inventory/parts/{pid}", headers=h)).json()


async def _books(db, pid):
    """(on hand, movement sum, layer units, layer value) for the part, read fresh."""
    await db.commit()          # end any read snapshot so we see what the API committed
    on_hand = (await db.execute(select(func.coalesce(func.sum(StockLevel.qty_available), 0))
                                .where(StockLevel.part_id == pid))).scalar() or 0
    moved = (await db.execute(select(func.coalesce(func.sum(StockMovement.qty), 0))
                              .where(StockMovement.part_id == pid))).scalar() or 0
    layers = (await db.execute(select(StockItemBatch).where(StockItemBatch.part_id == pid))).scalars().all()
    units = sum(float(b.qty_remaining or 0) for b in layers)
    value = sum(float(b.qty_remaining or 0) * float(b.unit_cost or 0) for b in layers)
    return float(on_hand), float(moved), units, value


async def _assert_books(db, pid):
    on_hand, moved, units, value = await _books(db, pid)
    assert moved == pytest.approx(on_hand), f"movements {moved} != on hand {on_hand}"
    assert units == pytest.approx(on_hand), f"cost layers hold {units} units, {on_hand} on hand"
    return on_hand, value


# ── 1. opening stock carries a cost ──────────────────────────────────────────
@pytest.mark.asyncio
async def test_opening_stock_adjust_uses_sent_unit_cost(client, tenant, db):
    loc = await _loc(client, tenant)
    pid = await _part(client, tenant, "QD-1A", cost_price=4, sell_price=20)
    r = await client.post(f"/api/inventory/parts/{pid}/adjust", headers=tenant, json={
        "location_id": loc, "qty": 10, "movement_type": "adjust", "unit_cost": 6.5})
    assert r.status_code == 200, r.text
    assert r.json()["qty_available"] == 10
    assert (await _get(client, tenant, pid))["avg_cost"] == pytest.approx(6.5)
    on_hand, value = await _assert_books(db, pid)
    assert value == pytest.approx(65.0)


@pytest.mark.asyncio
async def test_plain_receive_falls_back_to_catalog_cost(client, tenant, db):
    loc = await _loc(client, tenant)
    pid = await _part(client, tenant, "QD-1B", cost_price=4, sell_price=20)
    r = await client.post(f"/api/inventory/parts/{pid}/adjust", headers=tenant, json={
        "location_id": loc, "qty": 5, "movement_type": "receive"})
    assert r.status_code == 200, r.text
    assert (await _get(client, tenant, pid))["avg_cost"] == pytest.approx(4.0)
    _on_hand, value = await _assert_books(db, pid)
    assert value == pytest.approx(20.0)
    mv = (await db.execute(select(StockMovement).where(StockMovement.part_id == pid))).scalars().all()
    assert [m.unit_cost for m in mv] == [pytest.approx(4.0)]


@pytest.mark.asyncio
async def test_receive_prefers_current_average_over_catalog(client, tenant, db):
    loc = await _loc(client, tenant)
    pid = await _part(client, tenant, "QD-1C", cost_price=4)
    await client.post(f"/api/inventory/parts/{pid}/adjust", headers=tenant, json={
        "location_id": loc, "qty": 2, "movement_type": "adjust", "unit_cost": 10})
    await client.post(f"/api/inventory/parts/{pid}/adjust", headers=tenant, json={
        "location_id": loc, "qty": 2, "movement_type": "receive"})
    assert (await _get(client, tenant, pid))["avg_cost"] == pytest.approx(10.0)
    _on_hand, value = await _assert_books(db, pid)
    assert value == pytest.approx(40.0)


@pytest.mark.asyncio
async def test_opening_stock_cost_reaches_job_cogs(client, tenant, db):
    """The QA symptom: a sale from opening stock showed COGS 0 (markup 3456%)."""
    loc = await _loc(client, tenant)
    pid = await _part(client, tenant, "QD-1D", cost_price=0, sell_price=30)
    await client.post(f"/api/inventory/parts/{pid}/adjust", headers=tenant, json={
        "location_id": loc, "qty": 3, "movement_type": "adjust", "unit_cost": 12})
    part = (await db.execute(select(Part).where(Part.id == pid))).scalar_one()
    assert part.avg_cost == pytest.approx(12.0)


# ── 2. a product with stock is not archived ──────────────────────────────────
async def _stocked(client, h, sku, qty=3):
    loc = await _loc(client, h)
    pid = await _part(client, h, sku, cost_price=2)
    await client.post(f"/api/inventory/parts/{pid}/adjust", headers=h, json={
        "location_id": loc, "qty": qty, "movement_type": "receive"})
    return loc, pid


@pytest.mark.asyncio
async def test_bulk_set_active_0_refuses_stocked_product(client, tenant, db):
    _loc_id, stocked = await _stocked(client, tenant, "QD-2A")
    empty = await _part(client, tenant, "QD-2B")
    r = await client.post("/api/inventory/parts/bulk", headers=tenant, json={
        "part_ids": [stocked, empty], "action": "set_active", "number": 0})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["changed"] == 1
    assert [b["id"] for b in body["blocked"]] == [stocked]
    assert "still in stock" in body["blocked"][0]["reason"]
    await db.commit()
    rows = {p.id: p.active for p in (await db.execute(select(Part).where(Part.id.in_([stocked, empty])))).scalars()}
    assert rows == {stocked: True, empty: False}


@pytest.mark.asyncio
async def test_put_active_false_archives_or_refuses(client, tenant, db):
    _loc_id, stocked = await _stocked(client, tenant, "QD-2C")
    r = await client.put(f"/api/inventory/parts/{stocked}", headers=tenant,
                         json={"name": "Part QD-2C", "active": False})
    assert r.status_code == 409 and "still in stock" in r.text
    assert (await _get(client, tenant, stocked)).get("qty_available") == 3

    empty = await _part(client, tenant, "QD-2D")
    r = await client.put(f"/api/inventory/parts/{empty}", headers=tenant,
                         json={"name": "Part QD-2D", "active": False})
    assert r.status_code == 200, r.text
    await db.commit()
    assert (await db.execute(select(Part.active).where(Part.id == empty))).scalar_one() is False
    # and it can come back
    r = await client.put(f"/api/inventory/parts/{empty}", headers=tenant,
                         json={"name": "Part QD-2D", "active": True})
    assert r.status_code == 200, r.text
    await db.commit()
    assert (await db.execute(select(Part.active).where(Part.id == empty))).scalar_one() is True


@pytest.mark.asyncio
async def test_catalog_bulk_delete_refuses_stocked_product(client, tenant, db):
    _loc_id, stocked = await _stocked(client, tenant, "QD-2E")
    r = await client.post("/api/catalog/products/bulk", headers=tenant,
                          json={"ids": [stocked], "action": "delete"})
    assert r.status_code == 200, r.text
    assert r.json()["count"] == 0 and r.json()["blocked"] == ["Part QD-2E"]


# ── 5. stock written by postings and PO receipts names its warehouse ────────
async def _po(client, h, pid, qty, cost):
    sid = (await client.post("/api/suppliers", json={"name": f"Sup {pid[:6]}"}, headers=h)).json()["id"]
    loc = await _loc(client, h)
    po_id = (await client.post("/api/inventory/po", headers=h, json={
        "supplier_id": sid, "location_id": loc,
        "lines": [{"part_id": pid, "qty_ordered": qty, "unit_cost": cost}]})).json()["po_id"]
    line = (await client.get(f"/api/inventory/po/{po_id}", headers=h)).json()["lines"][0]["id"]
    return po_id, line


@pytest.mark.asyncio
async def test_posting_and_po_receipt_tag_the_default_warehouse(client, tenant, db):
    from backend.models import Warehouse
    loc = await _loc(client, tenant)
    pid = await _part(client, tenant, "QD-5A")
    r = await client.post("/api/inventory/postings", headers=tenant, json={
        "location_id": loc, "lines": [{"part_id": pid, "qty": 4, "purchase_price": 3}]})
    assert r.status_code == 201, r.text
    po_id, line = await _po(client, tenant, pid, 6, 3)
    r = await client.post(f"/api/inventory/po/{po_id}/receive", headers=tenant,
                          json={"lines": [{"po_line_id": line, "qty": 6}]})
    assert r.status_code == 200, r.text
    # a stock document on the same warehouse must not open a second, separately-valued row
    r = await client.post("/api/stock-docs/write-offs", headers=tenant, json={
        "location_id": loc, "reason": "damaged", "lines": [{"part_id": pid, "qty": 1}]})
    assert r.status_code == 201, r.text

    await db.commit()
    wid = (await db.execute(select(Warehouse.id).where(
        Warehouse.location_id == loc, Warehouse.kind == "product", Warehouse.is_default == True))).scalar_one()  # noqa: E712
    for model in (StockLevel, StockMovement, StockItemBatch):
        tags = set((await db.execute(select(model.warehouse_id).where(model.part_id == pid))).scalars())
        assert tags == {wid}, f"{model.__tablename__}: {tags}"
    assert len((await db.execute(select(StockLevel).where(StockLevel.part_id == pid))).scalars().all()) == 1
    on_hand, value = await _assert_books(db, pid)
    assert on_hand == 9 and value == pytest.approx(27.0)

    rep = (await client.get("/api/reports/run/stock_valuation", headers=tenant)).json()
    names = [row[0] for row in rep.get("rows", rep.get("data", []))]
    assert "—" not in names, names


def test_backfill_tags_and_merges_untagged_stock(tmp_path):
    """qad01: untagged rows join their location's default warehouse (merged into its own row when
    it has one), a bare location gets a warehouse, and a second run changes nothing."""
    import importlib.util

    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    spec = importlib.util.spec_from_file_location(
        "qad01", Path(__file__).parents[1] / "migrations/versions/qad01_stock_warehouse_backfill.py")
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)
    eng = sa.create_engine(f"sqlite:///{tmp_path}/b.db")
    with eng.begin() as conn:
        x = lambda q: conn.execute(sa.text(q))  # noqa: E731
        x("CREATE TABLE locations (id TEXT PRIMARY KEY, org_id TEXT, name TEXT)")
        x("CREATE TABLE warehouses (id TEXT PRIMARY KEY, org_id TEXT, name TEXT, kind TEXT, scope TEXT, "
          "location_id TEXT, is_default BOOLEAN, allowed_actions JSON, active BOOLEAN, created_at TIMESTAMP, "
          "deleted_at TIMESTAMP)")
        x("CREATE TABLE storage_bins (id TEXT PRIMARY KEY, warehouse_id TEXT)")
        x("CREATE TABLE stock_levels (id TEXT PRIMARY KEY, part_id TEXT, location_id TEXT, warehouse_id TEXT, "
          "bin_id TEXT, qty_available FLOAT, qty_reserved FLOAT)")
        x("CREATE UNIQUE INDEX uq_unbinned ON stock_levels (part_id, location_id, coalesce(warehouse_id, '')) "
          "WHERE bin_id IS NULL")
        x("CREATE TABLE stock_item_batches (id TEXT PRIMARY KEY, org_id TEXT, part_id TEXT, location_id TEXT, "
          "warehouse_id TEXT, bin_id TEXT, unit_cost NUMERIC, qty_received NUMERIC, qty_remaining NUMERIC)")
        x("CREATE TABLE stock_movements (id TEXT PRIMARY KEY, part_id TEXT, location_id TEXT, warehouse_id TEXT, "
          "movement_type TEXT, qty FLOAT)")
        x("INSERT INTO locations (id, org_id, name) VALUES ('L1', 'o1', 'Main'), ('L2', 'o1', 'Annex')")
        x("INSERT INTO warehouses (id, org_id, name, kind, scope, location_id, is_default, active) "
          "VALUES ('W1', 'o1', 'Main warehouse', 'product', 'location', 'L1', 1, 1)")
        x("INSERT INTO stock_levels (id, part_id, location_id, warehouse_id, bin_id, qty_available, qty_reserved) "
          "VALUES ('s-null', 'P1', 'L1', NULL, NULL, 4, 1), ('s-own', 'P1', 'L1', 'W1', NULL, 2, 0), "
          "('s-p2', 'P2', 'L1', NULL, NULL, 5, 0), ('s-l2', 'P2', 'L2', NULL, NULL, 3, 0)")
        x("INSERT INTO stock_item_batches (id, org_id, part_id, location_id, unit_cost, qty_received, qty_remaining) "
          "VALUES ('b1', 'o1', 'P1', 'L1', 2, 4, 4)")
        x("INSERT INTO stock_movements (id, part_id, location_id, movement_type, qty) "
          "VALUES ('m1', 'P1', 'L1', 'receive', 4), ('m2', 'P2', 'L2', 'receive', 3)")
        for _ in range(2):        # idempotent
            with Operations.context(MigrationContext.configure(conn)):
                mig.upgrade()
        rows = {r[0]: r[1:] for r in x("SELECT id, warehouse_id, qty_available, qty_reserved FROM stock_levels")}
        w2 = x("SELECT id FROM warehouses WHERE location_id = 'L2'").scalars().all()
        assert len(w2) == 1
        assert rows == {"s-own": ("W1", 6, 1), "s-p2": ("W1", 5, 0), "s-l2": (w2[0], 3, 0)}
        assert x("SELECT warehouse_id FROM stock_item_batches").scalar() == "W1"
        assert dict(x("SELECT id, warehouse_id FROM stock_movements").all()) == {"m1": "W1", "m2": w2[0]}


# ── 3. one write-off path: layers consumed, outflow logged negative, no silent cut ─────────
async def _two_layers(client, h, sku):
    """5 units at 2.00 then 5 at 4.00 → 10 on hand worth 30."""
    loc = await _loc(client, h)
    pid = await _part(client, h, sku)
    for cost in (2, 4):
        await client.post(f"/api/inventory/parts/{pid}/adjust", headers=h, json={
            "location_id": loc, "qty": 5, "movement_type": "receive", "unit_cost": cost})
    return loc, pid


@pytest.mark.asyncio
async def test_adjust_writeoff_consumes_fifo_layers(client, tenant, db):
    loc, pid = await _two_layers(client, tenant, "QD-3A")
    r = await client.post(f"/api/inventory/parts/{pid}/adjust", headers=tenant, json={
        "location_id": loc, "qty": 6, "movement_type": "writeoff"})
    assert r.status_code == 200, r.text
    assert r.json()["qty_available"] == 4
    on_hand, value = await _assert_books(db, pid)
    assert on_hand == 4 and value == pytest.approx(16.0)     # the oldest 5 @2 and one @4 went
    mv = (await db.execute(select(StockMovement).where(
        StockMovement.part_id == pid, StockMovement.movement_type == "writeoff"))).scalars().all()
    assert sum(m.qty for m in mv) == -6


@pytest.mark.asyncio
async def test_catalog_writeoff_logs_outflow_negative(client, tenant, db):
    loc, pid = await _two_layers(client, tenant, "QD-3B")
    r = await client.post(f"/api/catalog/products/{pid}/write-off", headers=tenant,
                          json={"location_id": loc, "qty": 3})
    assert r.status_code == 200, r.text
    on_hand, value = await _assert_books(db, pid)
    assert on_hand == 7 and value == pytest.approx(2 * 2 + 5 * 4)


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["adjust", "catalog"])
async def test_writeoff_more_than_stock_is_refused(client, tenant, db, path):
    loc, pid = await _two_layers(client, tenant, f"QD-3C-{path}")
    if path == "adjust":
        r = await client.post(f"/api/inventory/parts/{pid}/adjust", headers=tenant, json={
            "location_id": loc, "qty": 25, "movement_type": "writeoff"})
    else:
        r = await client.post(f"/api/catalog/products/{pid}/write-off", headers=tenant,
                              json={"location_id": loc, "qty": 25})
    assert r.status_code == 409, r.text
    assert "Only 10" in r.text
    on_hand, value = await _assert_books(db, pid)
    assert on_hand == 10 and value == pytest.approx(30.0)


@pytest.mark.asyncio
async def test_count_down_adjust_consumes_layers(client, tenant, db):
    loc, pid = await _two_layers(client, tenant, "QD-3D")
    r = await client.post("/api/inventory/parts/bulk-adjust", headers=tenant, json={
        "location_id": loc, "rows": [{"part_id": pid, "set_qty": 8}]})
    assert r.status_code == 200, r.text
    on_hand, value = await _assert_books(db, pid)
    assert on_hand == 8 and value == pytest.approx(3 * 2 + 5 * 4)
