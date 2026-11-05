"""Hardening council: archiving (deleting) a job releases its stock holds.

`_archive_order` put consumed parts back on the shelf but left the job's active StockReservation
rows and `StockLevel.qty_reserved` alone, so a deleted estimate kept blocking its units (up to
180 days) from every other job and transfer.
"""
import asyncio
import os
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select, update

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


async def _on_hand(db, pid):
    db.expire_all()
    a, r = (await db.execute(select(func.coalesce(func.sum(StockLevel.qty_available), 0),
                                    func.coalesce(func.sum(StockLevel.qty_reserved), 0))
                             .where(StockLevel.part_id == pid))).one()
    return float(a), float(r)


async def _work_order(client, h, loc):
    cid = (await client.post("/api/clients", json={"name": "Cust"}, headers=h)).json()["id"]
    r = await client.post("/api/orders", json={"client_id": cid, "location_id": loc}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


@_pg
@pytest.mark.asyncio
async def test_archiving_a_job_while_another_draws_its_hold_with_override(client, tenant, db, monkeypatch):
    """Independent review: the archive locked the job's holds and then its stock rows — the reverse
    of the work-order draw, which locks the stock rows and then takes units off the holds (an
    override). Archiving job A while job C drew A's held unit deadlocked (one came back 500).

    The window is forced: C pauses holding the stock rows, A's archive starts in the pause."""
    from backend.services import stock_consume
    loc = await _loc(client, tenant)
    pid = await _part(client, tenant, loc, 1)
    a = await _estimate(client, tenant, loc, pid, 1)
    assert (await client.post(f"/api/orders/{a}/reserve-stock", json={"hold_days": 30}, headers=tenant)).json()["reserved"]
    c = await _work_order(client, tenant, loc)
    orig = stock_consume.lock_rows
    drawing = asyncio.Event()
    calls = []

    async def paused(db_, part_id, location_id):
        res = await orig(db_, part_id, location_id)
        calls.append(1)
        if len(calls) == 1:
            drawing.set()
            await asyncio.sleep(0.7)
        return res
    monkeypatch.setattr(stock_consume, "lock_rows", paused)

    async def archive():
        await asyncio.wait_for(drawing.wait(), 10)
        return await client.delete(f"/api/orders/{a}", headers=tenant)
    draw, arch = await asyncio.gather(
        client.post(f"/api/orders/{c}/lines", headers=tenant, json={
            "line_type": "part", "kind": "product", "ref_id": pid, "description": "x", "qty": 1, "unit_price": 1}),
        archive())
    assert draw.status_code == 201, draw.text[:300]
    assert arch.status_code == 200, arch.text[:300]
    avail, reserved = await _on_hand(db, pid)
    assert (avail, reserved) == (0, 0), (avail, reserved)     # C took the unit; nothing is held any more
    assert await _active_holds(db, a) == []


async def _two_holds(client, tenant, db):
    """On hand 2: job A holds 1, job B holds 1. Returns (pid, a, b, A's hold id)."""
    loc = await _loc(client, tenant)
    pid = await _part(client, tenant, loc, 2)
    a = await _estimate(client, tenant, loc, pid, 1)
    b = await _estimate(client, tenant, loc, pid, 1)
    for job in (a, b):
        assert (await client.post(f"/api/orders/{job}/reserve-stock", json={"hold_days": 30}, headers=tenant)).json()["reserved"]
    assert (await _on_hand(db, pid))[1] == 2
    hold = (await db.execute(select(StockReservation.id).where(StockReservation.order_id == a))).scalar_one()
    return pid, a, b, hold


def _pause_first_release(monkeypatch):
    """The first release of a hold pauses before taking it off the stock row; returns an event set
    when that pause begins, so the racing archive can start inside it."""
    from backend.services import stock_rows
    orig = stock_rows.reserve
    started = asyncio.Event()
    calls = []

    async def paused(db_, part_id, location_id, delta):
        calls.append(1)
        if len(calls) == 1:
            started.set()
            await asyncio.sleep(0.7)
        return await orig(db_, part_id, location_id, delta)
    monkeypatch.setattr(stock_rows, "reserve", paused)
    return started


@_pg
@pytest.mark.asyncio
async def test_release_and_archive_of_the_same_hold_release_it_once(client, tenant, db, monkeypatch):
    """Independent review: the manual release read the hold without a lock, so racing the archive of
    its job both released it and `qty_reserved` went down twice — taking a unit off job B's hold."""
    pid, a, b, hold = await _two_holds(client, tenant, db)
    started = _pause_first_release(monkeypatch)

    async def archive():
        await asyncio.wait_for(started.wait(), 10)
        return await client.delete(f"/api/orders/{a}", headers=tenant)
    rel, arch = await asyncio.gather(
        client.post(f"/api/inventory/reservations/{hold}/release", headers=tenant), archive())
    assert rel.status_code in (200, 404), rel.text[:300]
    assert arch.status_code == 200, arch.text[:300]
    avail, reserved = await _on_hand(db, pid)
    assert (avail, reserved) == (2, 1), (avail, reserved)     # B's hold survives
    assert await _active_holds(db, a) == []
    assert len(await _active_holds(db, b)) == 1


@_pg
@pytest.mark.asyncio
async def test_expiry_sweep_and_archive_of_the_same_hold_release_it_once(client, tenant, db, monkeypatch):
    """Independent review: the expiry sweep read expired holds without a lock, so racing the archive
    of a hold's job released it twice and took a unit off job B's hold."""
    pid, a, b, hold = await _two_holds(client, tenant, db)
    await db.execute(update(StockReservation).where(StockReservation.id == hold)
                     .values(expires_at=now_utc() - timedelta(hours=1)))
    await db.commit()
    started = _pause_first_release(monkeypatch)

    async def archive():
        await asyncio.wait_for(started.wait(), 10)
        return await client.delete(f"/api/orders/{a}", headers=tenant)
    sweep, arch = await asyncio.gather(client.get("/api/inventory/reservations", headers=tenant), archive())
    assert sweep.status_code == 200, sweep.text[:300]
    assert arch.status_code == 200, arch.text[:300]
    avail, reserved = await _on_hand(db, pid)
    assert (avail, reserved) == (2, 1), (avail, reserved)     # B's hold survives
    assert await _active_holds(db, a) == []
    assert len(await _active_holds(db, b)) == 1


@_pg
@pytest.mark.asyncio
async def test_archiving_a_job_that_drew_one_product_and_holds_another_does_not_deadlock(
        client, tenant, db, monkeypatch):
    """Stock lock order is Order → StockLevel sorted by (part_id, location_id) → holds. The archive
    put the job's drawn parts back in line order and only then released its holds, so a job that
    drew Y and held X (X < Y) locked Y then X — the reverse of a reserve that needs [X, Y], which
    locks X then Y. Every stock row the archive touches is now locked sorted up front.

    The window is forced: the archive pauses after putting Y back, the reserve starts in the pause."""
    from backend.services import stock_rows
    loc = await _loc(client, tenant)
    x, y = sorted([await _part(client, tenant, loc, 5), await _part(client, tenant, loc, 5)])
    a = await _work_order(client, tenant, loc)
    r = await client.post(f"/api/orders/{a}/lines", headers=tenant, json={
        "line_type": "part", "kind": "product", "ref_id": y, "description": "y", "qty": 1, "unit_price": 1})
    assert r.status_code == 201, r.text
    r = await client.post("/api/inventory/reserve", headers=tenant, json={
        "part_id": x, "location_id": loc, "qty": 1, "order_id": a, "hold_days": 30})
    assert r.status_code == 201, r.text
    cid = (await client.post("/api/clients", json={"name": "Cust"}, headers=tenant)).json()["id"]
    r = await client.post("/api/orders", headers=tenant, json={
        "client_id": cid, "location_id": loc, "is_estimate": True,
        "lines": [{"line_type": "part", "kind": "product", "ref_id": x, "description": "x", "qty": 1, "unit_price": 1},
                  {"line_type": "part", "kind": "product", "ref_id": y, "description": "y", "qty": 1, "unit_price": 1}]})
    assert r.status_code == 201, r.text
    d = r.json()["id"]

    orig = stock_rows.add
    put_back = asyncio.Event()

    async def paused(db_, part_id, location_id, qty, **kw):
        res = await orig(db_, part_id, location_id, qty, **kw)
        if part_id == y and not put_back.is_set():
            put_back.set()
            await asyncio.sleep(0.7)        # the reserve locks X and waits on Y meanwhile
        return res
    monkeypatch.setattr(stock_rows, "add", paused)

    async def reserve():
        await asyncio.wait_for(put_back.wait(), 10)
        return await client.post(f"/api/orders/{d}/reserve-stock", json={"hold_days": 7}, headers=tenant)
    arch, res = await asyncio.gather(client.delete(f"/api/orders/{a}", headers=tenant), reserve())
    assert (arch.status_code, res.status_code) == (200, 201), (arch.text[:300], res.text[:300])
    assert await _active_holds(db, a) == []
    assert await _on_hand(db, x) == (5, 1)       # A's hold on X released, D holds one
    assert await _on_hand(db, y) == (5, 1)       # A's Y back on the shelf, D holds one
