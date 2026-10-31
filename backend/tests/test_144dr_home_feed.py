"""144dr — Home 'needs you' feed: real, org-scoped signals with deep links.
Empty shop => ok=True; a created signal shows up with the right count + link."""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from backend.services.dates import now_utc


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


@pytest.mark.asyncio
async def test_feed_empty_is_ok(client, tenant):
    r = await client.get("/api/reports/home-feed", headers=tenant)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "items" in body and isinstance(body["items"], list)
    # fresh tenant: nothing needs attention
    assert body["ok"] == (len(body["items"]) == 0)


@pytest.mark.asyncio
async def test_overdue_order_surfaces(client, tenant, db):
    from backend.models import Order, OrderStatus
    # The status-group mapping path must run over real rows (regression: OrderStatus key field,
    # not .status). Signup seeds the work-order statuses (launch QA B2); they must be there.
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    keys = set((await db.execute(select(OrderStatus.key).where(
        OrderStatus.org_id == me["org_id"], OrderStatus.record_type == "work_order"))).scalars())
    assert {"done", "ready"} <= keys
    loc = await _loc(client, tenant)
    cid = (await client.post("/api/clients", json={"name": "C"}, headers=tenant)).json()["id"]
    o = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x"}, headers=tenant)).json()
    # make it overdue
    row = (await db.execute(select(Order).where(Order.id == o["id"]))).scalar_one()
    row.due_at = now_utc() - timedelta(days=2)
    await db.commit()

    r = await client.get("/api/reports/home-feed", headers=tenant)
    body = r.json()
    overdue = [i for i in body["items"] if i["key"] == "overdue"]
    assert overdue, body
    assert overdue[0]["count"] >= 1
    assert overdue[0]["to"] == "/orders"
    assert body["ok"] is False


@pytest.mark.asyncio
async def test_overdue_count_is_accurate(client, tenant, db):
    """Only genuinely overdue, open orders are counted — a future-due and a closed
    order must not inflate the number. (Every query filters org_id, same tenant
    isolation the rest of the app is tested for.)"""
    from backend.models import Order
    loc = await _loc(client, tenant)
    cid = (await client.post("/api/clients", json={"name": "C"}, headers=tenant)).json()["id"]
    ids = [(await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": f"x{i}"}, headers=tenant)).json()["id"] for i in range(3)]
    now = now_utc()
    rows = {r.id: r for r in (await db.execute(select(Order).where(Order.id.in_(ids)))).scalars().all()}
    rows[ids[0]].due_at = now - timedelta(days=1)   # overdue
    rows[ids[1]].due_at = now + timedelta(days=3)   # future — not overdue
    rows[ids[2]].due_at = now - timedelta(days=1)
    rows[ids[2]].closed_at = now  # overdue date but closed
    await db.commit()

    body = (await client.get("/api/reports/home-feed", headers=tenant)).json()
    overdue = [i for i in body["items"] if i["key"] == "overdue"]
    assert overdue and overdue[0]["count"] == 1, overdue
