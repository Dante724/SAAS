"""144bm — job-driven 'Parts to order': aggregate parts needed across OPEN work orders
vs on-hand + on-order, grouped by supplier. Read-only (writes nothing)."""
import pytest

from backend.models import Order, OrderLine


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


async def _part(client, h, name, sku):
    return (await client.post("/api/inventory/parts", json={"name": name, "sku": sku, "barcode": sku}, headers=h)).json()


@pytest.mark.asyncio
async def test_to_order_lists_shortfall_from_open_job(client, tenant, db):
    from sqlalchemy import select

    from backend.models import Part
    needed = await _part(client, tenant, "Need Screen", "NEED-SCR")
    org_id = (await db.execute(select(Part.org_id).where(Part.id == needed["id"]))).scalar_one()
    loc = await _loc(client, tenant)
    # an OPEN work order that needs 3 of a part we have 0 of
    o = Order(org_id=org_id, order_number="WO-NEED-1", status="new", location_id=loc)
    db.add(o)
    await db.flush()
    db.add(OrderLine(order_id=o.id, line_type="part", ref_id=needed["id"], description="Need Screen", qty=3))
    await db.commit()

    r = await client.get("/api/inventory/to-order", headers=tenant)
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    row = next((x for x in items if x["part_id"] == needed["id"]), None)
    assert row is not None
    assert row["needed"] == 3 and row["on_hand"] == 0 and row["shortfall"] == 3
    assert "WO-NEED-1" in row["jobs"]


@pytest.mark.asyncio
async def test_in_stock_part_not_listed(client, tenant, db):
    from sqlalchemy import select

    from backend.models import Part
    loc = await _loc(client, tenant)
    p = await _part(client, tenant, "Have Battery", "HAVE-BAT")
    org_id = (await db.execute(select(Part.org_id).where(Part.id == p["id"]))).scalar_one()
    await client.post("/api/inventory/postings", json={"location_id": loc, "lines": [{"part_id": p["id"], "qty": 10, "purchase_price": 5}]}, headers=tenant)
    o = Order(org_id=org_id, order_number="WO-HAVE-1", status="new", location_id=loc)
    db.add(o)
    await db.flush()
    db.add(OrderLine(order_id=o.id, line_type="part", ref_id=p["id"], description="Have Battery", qty=2))
    await db.commit()

    items = (await client.get("/api/inventory/to-order", headers=tenant)).json()["items"]
    assert all(x["part_id"] != p["id"] for x in items)  # 10 on hand covers 2 needed


@pytest.mark.asyncio
async def test_closed_order_excluded(client, tenant, db):
    from sqlalchemy import select

    from backend.models import Part
    p = await _part(client, tenant, "Closed Need", "CLOSED-NEED")
    org_id = (await db.execute(select(Part.org_id).where(Part.id == p["id"]))).scalar_one()
    loc = await _loc(client, tenant)
    o = Order(org_id=org_id, order_number="WO-CLOSED-1", status="closed", location_id=loc)
    db.add(o)
    await db.flush()
    db.add(OrderLine(order_id=o.id, line_type="part", ref_id=p["id"], description="Closed Need", qty=4))
    await db.commit()

    items = (await client.get("/api/inventory/to-order", headers=tenant)).json()["items"]
    assert all(x["part_id"] != p["id"] for x in items)  # closed job doesn't need ordering
