"""Hardening council M7: `_release_expired` only ran from the reservations list, so an expired
hold kept `StockLevel.qty_reserved` high and every `free()` path (manual reserve, job reserve,
transfer "ready") read it as still blocking until someone happened to open that screen.
"""
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from backend.models import StockLevel, StockReservation
from backend.services.dates import now_utc


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


async def _loc2(client, h):
    r = await client.post("/api/staff/locations", json={"name": "Branch B"}, headers=h)
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def _part(client, h):
    body = {"name": f"P-{uuid.uuid4().hex[:6]}", "sku": f"SKU-{uuid.uuid4().hex[:6]}",
            "cost_price": 10, "sell_price": 100}
    r = await client.post("/api/catalog/products", json=body, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _receive(client, h, loc, pid, qty):
    r = await client.post(f"/api/inventory/parts/{pid}/adjust", json={
        "location_id": loc, "qty": qty, "movement_type": "receive"}, headers=h)
    assert r.status_code == 200, r.text


async def _expired_hold(client, h, db, loc, pid, qty):
    r = await client.post("/api/inventory/reserve", json={"part_id": pid, "location_id": loc, "qty": qty,
                                                         "hold_days": 1}, headers=h)
    assert r.status_code == 201, r.text
    hold = (await db.execute(select(StockReservation).where(StockReservation.id == r.json()["reservation_id"]))).scalar_one()
    hold.expires_at = now_utc() - timedelta(days=3)
    await db.commit()
    return hold.id


async def _reserved(db, pid):
    return float((await db.execute(select(func.coalesce(func.sum(StockLevel.qty_reserved), 0)).where(
        StockLevel.part_id == pid))).scalar())


async def _order(client, h, loc, pid, qty):
    cid = (await client.post("/api/clients", json={"name": "Cust"}, headers=h)).json()["id"]
    r = await client.post("/api/orders", json={
        "client_id": cid, "location_id": loc, "is_estimate": True,   # a work order draws its parts on create
        "lines": [{"line_type": "part", "kind": "product", "ref_id": pid, "description": "x", "qty": qty, "unit_price": 1}]},
        headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.mark.asyncio
async def test_expired_hold_does_not_block_a_transfer(client, tenant, db):
    a, b = await _loc(client, tenant), await _loc2(client, tenant)
    pid = await _part(client, tenant)
    await _receive(client, tenant, a, pid, 2)
    await _expired_hold(client, tenant, db, a, pid, 2)
    t = (await client.post("/api/transfers", json={"from_location_id": a, "to_location_id": b,
                                                    "lines": [{"part_id": pid, "qty": 2}]}, headers=tenant)).json()["id"]
    r = await client.patch(f"/api/transfers/{t}/status", json={"status": "ready"}, headers=tenant)
    assert r.status_code == 200, f"expired hold still blocks: {r.status_code} {r.text}"
    db.expire_all()
    assert await _reserved(db, pid) == 0


@pytest.mark.asyncio
async def test_expired_hold_does_not_block_a_new_reservation(client, tenant, db):
    loc = await _loc(client, tenant)
    pid = await _part(client, tenant)
    await _receive(client, tenant, loc, pid, 2)
    old = await _expired_hold(client, tenant, db, loc, pid, 2)
    r = await client.post("/api/inventory/reserve", json={"part_id": pid, "location_id": loc, "qty": 2,
                                                         "hold_days": 7}, headers=tenant)
    assert r.status_code == 201, f"expired hold still blocks: {r.status_code} {r.text}"
    db.expire_all()
    assert await _reserved(db, pid) == 2          # the new hold only, the old one is gone
    assert (await db.execute(select(StockReservation.released).where(StockReservation.id == old))).scalar() is True


@pytest.mark.asyncio
async def test_expired_hold_does_not_block_a_job_reservation(client, tenant, db):
    loc = await _loc(client, tenant)
    pid = await _part(client, tenant)
    await _receive(client, tenant, loc, pid, 2)
    await _expired_hold(client, tenant, db, loc, pid, 2)
    oid = await _order(client, tenant, loc, pid, 2)
    r = await client.post(f"/api/orders/{oid}/reserve-stock", json={"hold_days": 7}, headers=tenant)
    assert r.status_code == 201, r.text
    assert r.json()["short"] == [], f"expired hold still blocks: {r.json()}"
    assert sum(x["qty"] for x in r.json()["reserved"]) == 2
    db.expire_all()
    assert await _reserved(db, pid) == 2


@pytest.mark.asyncio
async def test_live_hold_still_blocks(client, tenant, db):
    a, b = await _loc(client, tenant), await _loc2(client, tenant)
    pid = await _part(client, tenant)
    await _receive(client, tenant, a, pid, 2)
    r = await client.post("/api/inventory/reserve", json={"part_id": pid, "location_id": a, "qty": 2,
                                                         "hold_days": 7}, headers=tenant)
    assert r.status_code == 201, r.text
    t = (await client.post("/api/transfers", json={"from_location_id": a, "to_location_id": b,
                                                    "lines": [{"part_id": pid, "qty": 2}]}, headers=tenant)).json()["id"]
    r = await client.patch(f"/api/transfers/{t}/status", json={"status": "ready"}, headers=tenant)
    assert r.status_code == 409, r.text
    assert await _reserved(db, pid) == 2
