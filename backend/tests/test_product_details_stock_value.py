"""Design audit BUG-04: a product's Details tab said "Avg cost 0 / Stock value 0" while its Stock tab
said 80,000 for the same 25 units.

Details multiplied the raw `avg_cost` column on the screen, and that column was 0 for stock that
never went through the inflow path (the demo seed wrote stock rows directly). The Stock tab and the
stock-value reports value stock at one cost basis (moving average, else the list cost). Details now
gets that same basis and value from the server, and the seed brings its stock in through the one
inflow path, so the average and the cost layers are set where the stock is written.
"""
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from backend.models import Part, StockItemBatch, StockLevel


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


async def _product(client, h, name, cost):
    r = await client.post("/api/catalog/products", json={"name": name, "cost_price": cost, "sell_price": cost * 2},
                          headers=h)
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def _details(client, h, pid):
    r = await client.get(f"/api/catalog/products/{pid}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()["product"]


async def _stock_tab(client, h, pid):
    r = await client.get(f"/api/inventory/parts/{pid}/stock", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


async def _valuation_total(client, h):
    r = await client.get("/api/reports/run/stock_valuation", headers=h)
    assert r.status_code == 200, r.text
    return next(row for row in r.json()["rows"] if row[0] == "TOTAL")[-1]


@pytest.mark.asyncio
async def test_details_matches_stock_tab_and_report_after_receiving_at_a_cost(client, tenant):
    loc = await _loc(client, tenant)
    pid = await _product(client, tenant, "MacBook Keyboard", 3200)
    for qty, cost in ((10, 3500), (10, 2500)):
        r = await client.post(f"/api/inventory/parts/{pid}/adjust", headers=tenant, json={
            "location_id": loc, "qty": qty, "movement_type": "receive", "unit_cost": cost})
        assert r.status_code == 200, r.text

    p = await _details(client, tenant, pid)
    tab = await _stock_tab(client, tenant, pid)
    assert p["avg_cost"] == pytest.approx(3000)          # the receipts moved the average
    assert p["unit_cost"] == pytest.approx(tab["unit_cost"]) == pytest.approx(3000), (p, tab)
    assert p["stock_value"] == pytest.approx(tab["total_value"]) == pytest.approx(60000), (p, tab)
    assert p["stock_value"] == pytest.approx(await _valuation_total(client, tenant))


@pytest.mark.asyncio
async def test_details_values_stock_that_never_went_through_a_receipt(client, tenant, db):
    """Stock written straight into the stock table (old seeds, old data) has no average yet: Details
    falls back to the list cost exactly like the Stock tab and the report, instead of showing 0."""
    loc = await _loc(client, tenant)
    pid = await _product(client, tenant, "MacBook Keyboard", 3200)
    db.add(StockLevel(part_id=pid, location_id=loc, qty_available=25, qty_reserved=0))
    await db.commit()

    p = await _details(client, tenant, pid)
    tab = await _stock_tab(client, tenant, pid)
    assert p["qty_available"] == 25
    assert p["unit_cost"] == pytest.approx(tab["unit_cost"]) == pytest.approx(3200), (p, tab)
    assert p["stock_value"] == pytest.approx(tab["total_value"]) == pytest.approx(80000), (p, tab)
    assert p["stock_value"] == pytest.approx(await _valuation_total(client, tenant))


@pytest.mark.asyncio
async def test_list_rows_carry_the_same_cost_basis(client, tenant, db):
    loc = await _loc(client, tenant)
    pid = await _product(client, tenant, "Thermal Paste", 80)
    db.add(StockLevel(part_id=pid, location_id=loc, qty_available=20, qty_reserved=0))
    await db.commit()
    items = (await client.get("/api/catalog/products", headers=tenant)).json()["items"]
    row = next(i for i in items if i["id"] == pid)
    assert (row["unit_cost"], row["stock_value"]) == pytest.approx((80, 1600)), row


@pytest.mark.asyncio
async def test_staff_who_cant_see_costs_get_no_value(client, tenant):
    """The new figures are cost figures: the redaction that hides avg_cost hides them too."""
    from backend.services.redact import redact
    out = redact({"product": {"name": "x", "avg_cost": 1, "unit_cost": 1, "stock_value": 25}})
    assert out == {"product": {"name": "x"}}, out


@pytest.mark.asyncio
async def test_demo_seed_stock_has_its_average_cost_and_cost_layers(db, monkeypatch):
    import scripts.seed as seed
    monkeypatch.setattr(seed, "AsyncSessionLocal", async_sessionmaker(db.bind, expire_on_commit=False))
    await seed.seed_demo_data()

    parts = (await db.execute(select(Part).where(Part.sku == "KB-MBP14"))).scalars().all()
    assert len(parts) == 1
    kb = parts[0]
    on_hand = (await db.execute(select(func.sum(StockLevel.qty_available))
                                .where(StockLevel.part_id == kb.id))).scalar()
    layers = (await db.execute(select(StockItemBatch).where(StockItemBatch.part_id == kb.id))).scalars().all()
    assert on_hand == 25
    assert kb.avg_cost == pytest.approx(3200), kb.avg_cost
    assert sum(b.qty_remaining for b in layers) == pytest.approx(25)
    assert sum(b.qty_remaining * b.unit_cost for b in layers) == pytest.approx(80000)
