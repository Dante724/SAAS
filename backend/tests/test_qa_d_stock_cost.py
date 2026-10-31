"""QA fix D (31 Oct 2026) — stock cost & valuation.

Every test ends on the same two books checks: stock movements add up to what is on hand, and the
FIFO cost layers hold exactly the on-hand units (so COGS and valuation are drawn from real costs).
"""
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
