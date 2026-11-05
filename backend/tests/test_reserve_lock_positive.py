"""Hardening council: stock reservations lock the rows they count and only hold positive amounts.

`POST /api/inventory/reserve` and `POST /api/orders/{id}/reserve-stock` read availability with no
lock, checked it in Python, then bumped `qty_reserved`: two reserves of the last unit both got 201
(reserved 2 > on hand 1). `ReserveIn.qty` had no lower bound, so qty=-3 "reserved" a negative hold
and released other jobs' stock.
"""
import asyncio
import os
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from backend.models import StockLevel, StockReservation
from backend.services.dates import now_utc

_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL", "")
_pg = pytest.mark.skipif(not _URL.startswith("postgresql"), reason="row-lock race needs Postgres")


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


async def _part(client, h, loc, qty):
    body = {"name": f"P-{uuid.uuid4().hex[:6]}", "sku": f"SKU-{uuid.uuid4().hex[:6]}", "cost_price": 10, "sell_price": 100}
    pid = (await client.post("/api/catalog/products", json=body, headers=h)).json()["id"]
    r = await client.post(f"/api/inventory/parts/{pid}/adjust", headers=h,
                          json={"location_id": loc, "qty": qty, "movement_type": "receive"})
    assert r.status_code == 200, r.text
    return pid


async def _on_hand(db, pid):
    a, r = (await db.execute(select(func.coalesce(func.sum(StockLevel.qty_available), 0),
                                    func.coalesce(func.sum(StockLevel.qty_reserved), 0))
                             .where(StockLevel.part_id == pid))).one()
    return float(a), float(r)


async def _estimate(client, h, loc, pid, qty):
    cid = (await client.post("/api/clients", json={"name": "Cust"}, headers=h)).json()["id"]
    r = await client.post("/api/orders", json={
        "client_id": cid, "location_id": loc, "is_estimate": True,
        "lines": [{"line_type": "part", "kind": "product", "ref_id": pid, "description": "x", "qty": qty, "unit_price": 1}]},
        headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


@_pg
@pytest.mark.asyncio
async def test_concurrent_reserves_cannot_hold_more_than_on_hand(client, tenant, db):
    loc = await _loc(client, tenant)
    pid = await _part(client, tenant, loc, 1)
    body = {"part_id": pid, "location_id": loc, "qty": 1, "hold_days": 7}
    r1, r2 = await asyncio.gather(client.post("/api/inventory/reserve", json=body, headers=tenant),
                                  client.post("/api/inventory/reserve", json=body, headers=tenant))
    codes = sorted([r1.status_code, r2.status_code])
    avail, reserved = await _on_hand(db, pid)
    assert reserved <= avail, f"reserved={reserved} > on_hand={avail}; statuses={codes}"
    assert codes == [201, 409], codes


@_pg
@pytest.mark.asyncio
async def test_concurrent_order_reserves_cannot_hold_more_than_on_hand(client, tenant, db):
    loc = await _loc(client, tenant)
    pid = await _part(client, tenant, loc, 1)
    a = await _estimate(client, tenant, loc, pid, 1)
    b = await _estimate(client, tenant, loc, pid, 1)
    r1, r2 = await asyncio.gather(client.post(f"/api/orders/{a}/reserve-stock", json={"hold_days": 7}, headers=tenant),
                                  client.post(f"/api/orders/{b}/reserve-stock", json={"hold_days": 7}, headers=tenant))
    assert r1.status_code == 201 and r2.status_code == 201, (r1.text, r2.text)
    held = sum(x["qty"] for r in (r1, r2) for x in r.json()["reserved"])
    avail, reserved = await _on_hand(db, pid)
    assert held == 1 and reserved <= avail, f"held={held} reserved={reserved} on_hand={avail}"


@pytest.mark.asyncio
async def test_negative_or_zero_reserve_qty_is_rejected(client, tenant, db):
    loc = await _loc(client, tenant)
    pid = await _part(client, tenant, loc, 3)
    job = await _estimate(client, tenant, loc, pid, 3)
    assert (await client.post(f"/api/orders/{job}/reserve-stock", json={"hold_days": 7}, headers=tenant)).json()["reserved"]
    assert (await _on_hand(db, pid))[1] == 3
    for qty in (-3, 0):
        r = await client.post("/api/inventory/reserve", json={"part_id": pid, "location_id": loc, "qty": qty}, headers=tenant)
        assert r.status_code == 422, f"reserve qty={qty} -> {r.status_code} {r.text}"
    db.expire_all()
    assert (await _on_hand(db, pid))[1] == 3, "another job's hold was released"
    negative = (await db.execute(select(func.count()).select_from(StockReservation).where(
        StockReservation.part_id == pid, StockReservation.qty <= 0))).scalar()
    assert negative == 0


@pytest.mark.asyncio
async def test_reserve_rejects_another_shops_order_and_clamps_hold_days(client, tenant, db):
    loc = await _loc(client, tenant)
    pid = await _part(client, tenant, loc, 5)
    r = await client.post("/api/auth/register", json={
        "org_name": "Other Co", "name": "Owner", "email": f"o-{uuid.uuid4().hex[:8]}@t.dev", "password": "pw-test-12345"})
    other = {"Authorization": f"Bearer {r.json()['access_token']}"}
    other_pid = await _part(client, other, await _loc(client, other), 1)
    other_job = await _estimate(client, other, await _loc(client, other), other_pid, 1)
    r = await client.post("/api/inventory/reserve", json={"part_id": pid, "location_id": loc, "qty": 1,
                                                          "order_id": other_job}, headers=tenant)
    assert r.status_code == 404, r.text
    assert (await _on_hand(db, pid))[1] == 0
    r = await client.post("/api/inventory/reserve", json={"part_id": pid, "location_id": loc, "qty": 1,
                                                          "hold_days": 100000}, headers=tenant)
    assert r.status_code == 201, r.text
    hold = (await db.execute(select(StockReservation).where(StockReservation.id == r.json()["reservation_id"]))).scalar_one()
    assert hold.expires_at <= now_utc() + timedelta(days=181)
    r = await client.post("/api/inventory/reserve", json={"part_id": pid, "location_id": loc, "qty": 1,
                                                          "hold_days": -5}, headers=tenant)
    assert r.status_code == 201, r.text
    hold = (await db.execute(select(StockReservation).where(StockReservation.id == r.json()["reservation_id"]))).scalar_one()
    assert hold.expires_at >= now_utc() + timedelta(hours=23)


@_pg
@pytest.mark.asyncio
async def test_two_reserves_of_one_job_at_once_hold_its_need_once(client, tenant, db, monkeypatch):
    """Independent review: reserve-stock read the job's existing holds without locking the job, so
    two clicks at once each saw "nothing held yet" and together held 2 units for a job that needs 1.
    The window is forced: the first request pauses after reading the holds, the second runs through."""
    from backend.routers import orders as orders_router
    loc = await _loc(client, tenant)
    pid = await _part(client, tenant, loc, 5)
    job = await _estimate(client, tenant, loc, pid, 1)
    orig = orders_router.names_by_id
    calls = []

    async def paused(*a, **kw):
        res = await orig(*a, **kw)
        calls.append(1)
        if len(calls) == 1:
            await asyncio.sleep(0.5)
        return res
    monkeypatch.setattr(orders_router, "names_by_id", paused)
    r1, r2 = await asyncio.gather(client.post(f"/api/orders/{job}/reserve-stock", json={"hold_days": 7}, headers=tenant),
                                  client.post(f"/api/orders/{job}/reserve-stock", json={"hold_days": 7}, headers=tenant))
    assert r1.status_code == 201 and r2.status_code == 201, (r1.text, r2.text)
    held = sum(x["qty"] for r in (r1, r2) for x in r.json()["reserved"])
    avail, reserved = await _on_hand(db, pid)
    assert held == 1 and reserved == 1, f"held={held} reserved={reserved} on_hand={avail}"
    active = (await db.execute(select(func.coalesce(func.sum(StockReservation.qty), 0)).where(
        StockReservation.order_id == job, StockReservation.released == False))).scalar()  # noqa: E712
    assert float(active) == 1


@_pg
@pytest.mark.asyncio
async def test_two_jobs_reserving_the_same_products_in_opposite_order_do_not_deadlock(client, tenant, db, monkeypatch):
    """Independent review: reserve-stock locked each product's stock rows in the job's line order,
    so a job needing [X, Y] and one needing [Y, X] at once deadlocked (one came back 500). The
    rows are locked in product-id order now. The window is forced: the first job pauses holding
    its first product's rows while the other job takes its own first product."""
    from backend.services import stock_rows
    loc = await _loc(client, tenant)
    x, y = sorted([await _part(client, tenant, loc, 5), await _part(client, tenant, loc, 5)])
    cid = (await client.post("/api/clients", json={"name": "Cust"}, headers=tenant)).json()["id"]
    jobs = []
    for first, second in ((x, y), (y, x)):
        r = await client.post("/api/orders", json={
            "client_id": cid, "location_id": loc, "is_estimate": True,
            "lines": [{"line_type": "part", "kind": "product", "ref_id": first, "description": "a", "qty": 1, "unit_price": 1},
                      {"line_type": "part", "kind": "product", "ref_id": second, "description": "b", "qty": 1, "unit_price": 1}]},
            headers=tenant)
        assert r.status_code == 201, r.text
        jobs.append(r.json()["id"])
    orig = stock_rows.rows
    locked = []

    async def paused(db_, part_id, location_id, *, lock=False):
        res = await orig(db_, part_id, location_id, lock=lock)
        if lock:
            locked.append(part_id)
            if len(locked) == 1:
                await asyncio.sleep(0.5)      # the other job locks its first product meanwhile
        return res
    monkeypatch.setattr(stock_rows, "rows", paused)
    r1, r2 = await asyncio.gather(*[client.post(f"/api/orders/{j}/reserve-stock", json={"hold_days": 7}, headers=tenant)
                                    for j in jobs])
    assert (r1.status_code, r2.status_code) == (201, 201), (r1.text[:300], r2.text[:300])
    for pid in (x, y):
        avail, reserved = await _on_hand(db, pid)
        assert (avail, reserved) == (5, 2), (pid, avail, reserved)
