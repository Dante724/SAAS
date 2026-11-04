"""Hardening council: archiving (deleting) a job releases its stock holds.

`_archive_order` put consumed parts back on the shelf but left the job's active StockReservation
rows and `StockLevel.qty_reserved` alone, so a deleted estimate kept blocking its units (up to
180 days) from every other job and transfer.
"""
import uuid

import pytest
from sqlalchemy import func, select

from backend.models import StockLevel, StockReservation


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


async def _part(client, h, loc, qty):
    body = {"name": f"P-{uuid.uuid4().hex[:6]}", "sku": f"SKU-{uuid.uuid4().hex[:6]}", "cost_price": 10, "sell_price": 100}
    pid = (await client.post("/api/catalog/products", json=body, headers=h)).json()["id"]
    r = await client.post(f"/api/inventory/parts/{pid}/adjust", headers=h,
                          json={"location_id": loc, "qty": qty, "movement_type": "receive"})
    assert r.status_code == 200, r.text
    return pid


async def _reserved(db, pid):
    return float((await db.execute(select(func.coalesce(func.sum(StockLevel.qty_reserved), 0))
                                   .where(StockLevel.part_id == pid))).scalar())


async def _active_holds(db, order_id):
    db.expire_all()
    return (await db.execute(select(StockReservation).where(
        StockReservation.order_id == order_id, StockReservation.released == False))).scalars().all()  # noqa: E712


async def _estimate(client, h, loc, pid, qty):
    cid = (await client.post("/api/clients", json={"name": "Cust"}, headers=h)).json()["id"]
    r = await client.post("/api/orders", json={
        "client_id": cid, "location_id": loc, "is_estimate": True,
        "lines": [{"line_type": "part", "kind": "product", "ref_id": pid, "description": "x", "qty": qty, "unit_price": 1}]},
        headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.mark.asyncio
async def test_deleting_a_job_releases_its_holds(client, tenant, db):
    loc = await _loc(client, tenant)
    pid = await _part(client, tenant, loc, 3)
    job = await _estimate(client, tenant, loc, pid, 3)
    r = await client.post(f"/api/orders/{job}/reserve-stock", json={"hold_days": 180}, headers=tenant)
    assert r.status_code == 201 and r.json()["reserved"], r.text
    assert await _reserved(db, pid) == 3

    r = await client.delete(f"/api/orders/{job}", headers=tenant)
    assert r.status_code == 200, r.text
    db.expire_all()
    assert await _reserved(db, pid) == 0
    assert await _active_holds(db, job) == []
    # the stock is reservable by the next job again
    r = await client.post("/api/inventory/reserve", json={"part_id": pid, "location_id": loc, "qty": 3}, headers=tenant)
    assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_bulk_archive_releases_holds_and_restore_does_not_re_reserve(client, tenant, db):
    loc = await _loc(client, tenant)
    pid = await _part(client, tenant, loc, 2)
    job = await _estimate(client, tenant, loc, pid, 2)
    assert (await client.post(f"/api/orders/{job}/reserve-stock", json={"hold_days": 30}, headers=tenant)).json()["reserved"]
    r = await client.post("/api/orders/bulk", json={"order_ids": [job], "action": "archive", "reason": "dup"}, headers=tenant)
    assert r.status_code == 200, r.text
    db.expire_all()
    assert await _reserved(db, pid) == 0
    assert await _active_holds(db, job) == []
    # restore un-hides only: the hold is gone for good (reserve again by hand if still wanted)
    r = await client.post(f"/api/orders/{job}/restore", headers=tenant)
    assert r.status_code == 200, r.text
    db.expire_all()
    assert await _reserved(db, pid) == 0
    assert await _active_holds(db, job) == []
