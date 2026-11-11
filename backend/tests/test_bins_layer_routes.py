"""Hardening council: the /api/bins cost-layer routes must not bypass stock.

`POST /api/bins/batches` used to write a StockItemBatch (+ serialized units) with no StockLevel
and no StockMovement, so units sat "in stock" that no shelf count knew about. `/bins/consume`
drained FIFO layers (open to any employee) and `/bins/allocate-serial` flipped a unit to
allocated, neither moving a single unit of stock. Nothing in the app calls the last two.
"""
import asyncio
import os
import uuid

import pytest
from sqlalchemy import func, select

from backend.models import Employee, Role, SerializedItem, StockItemBatch, StockLevel, StockMovement
from backend.services.auth import hash_password

_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL", "")
_PG = pytest.mark.skipif(not _URL.startswith("postgresql"), reason="needs concurrent writers (Postgres)")


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


async def _part(client, h, serialized=False):
    body = {"name": f"P-{uuid.uuid4().hex[:6]}", "sku": f"SKU-{uuid.uuid4().hex[:6]}",
            "cost_price": 10, "sell_price": 100, "serialized": serialized}
    r = await client.post("/api/catalog/products", json=body, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _sum(db, col, pid):
    return float((await db.execute(select(func.coalesce(func.sum(col), 0)).where(
        col.class_.part_id == pid))).scalar())


@pytest.mark.asyncio
async def test_bins_batches_moves_stock_with_the_layer(client, tenant, db):
    loc = await _loc(client, tenant)
    pid = await _part(client, tenant, serialized=True)
    r = await client.post("/api/bins/batches", json={
        "part_id": pid, "location_id": loc, "unit_cost": 50, "qty": 2,
        "serials": ["IMEI-A1", "IMEI-A2"]}, headers=tenant)
    assert r.status_code == 201, r.text
    on_hand = await _sum(db, StockLevel.qty_available, pid)
    layers = await _sum(db, StockItemBatch.qty_remaining, pid)
    moves = await _sum(db, StockMovement.qty, pid)
    units = (await db.execute(select(func.count()).select_from(SerializedItem).where(
        SerializedItem.part_id == pid, SerializedItem.status == "in_stock"))).scalar()
    # invariant: on-hand == in-stock units == cost layers == sum of movements
    assert (on_hand, units, layers, moves) == (2, 2, 2, 2), \
        f"on_hand={on_hand} units={units} layers={layers} movements={moves}"
    prod = (await client.get(f"/api/inventory/parts/{pid}", headers=tenant)).json()
    assert prod["qty_available"] == 2
    assert float(prod.get("avg_cost") or 0) == 50


@pytest.mark.asyncio
async def test_bins_batches_refuses_another_shops_location_and_bad_quantities(client, tenant, db):
    pid = await _part(client, tenant)
    r = await client.post("/api/auth/register", json={
        "org_name": "Other Co", "name": "Owner", "email": f"o-{uuid.uuid4().hex[:8]}@t.dev",
        "password": "pw-test-12345"})
    other_loc = await _loc(client, {"Authorization": f"Bearer {r.json()['access_token']}"})
    r = await client.post("/api/bins/batches", json={
        "part_id": pid, "location_id": other_loc, "unit_cost": 5, "qty": 1}, headers=tenant)
    assert r.status_code == 404, r.text
    loc = await _loc(client, tenant)
    for bad in ({"qty": 0}, {"qty": -2}, {"unit_cost": -1}):
        r = await client.post("/api/bins/batches", json={"part_id": pid, "location_id": loc, "unit_cost": 5,
                                                         "qty": 1, **bad}, headers=tenant)
        assert r.status_code == 422, (bad, r.text)
    assert await _sum(db, StockLevel.qty_available, pid) == 0


@pytest.mark.asyncio
async def test_bins_consume_and_allocate_serial_no_longer_exist(client, tenant, db):
    loc = await _loc(client, tenant)
    pid = await _part(client, tenant)
    r = await client.post(f"/api/inventory/parts/{pid}/adjust", headers=tenant,
                          json={"location_id": loc, "qty": 5, "movement_type": "receive", "unit_cost": 10})
    assert r.status_code == 200, r.text
    r = await client.post("/api/bins/consume", json={"part_id": pid, "qty": 5}, headers=tenant)
    assert r.status_code in (404, 405), r.text
    assert (await _sum(db, StockLevel.qty_available, pid), await _sum(db, StockItemBatch.qty_remaining, pid)) == (5, 5)
    cid = (await client.post("/api/clients", json={"name": "Cust"}, headers=tenant)).json()["id"]
    oid = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc}, headers=tenant)).json()["id"]
    r = await client.post("/api/bins/allocate-serial", json={"part_id": pid, "order_id": oid}, headers=tenant)
    assert r.status_code in (404, 405), r.text


@pytest.mark.asyncio
async def test_bins_batches_respects_the_warehouse_post_guard(client, tenant, db):
    """Independent review: /bins/batches is a receipt, so the warehouse must allow 'post' and
    this employee must be allowed to post there — the same guard /inventory/parts/{id}/adjust
    applies. An employee granted only 'writeoff' in the warehouse used to get a 201."""
    loc = await _loc(client, tenant)
    pid = await _part(client, tenant)
    whs = (await client.get("/api/settings/warehouses?kind=product", headers=tenant)).json()["items"]
    wh = next(w for w in whs if w["is_default"])
    org_id = (await client.get("/api/auth/me", headers=tenant)).json()["org_id"]
    role = Role(org_id=org_id, name="Stock", permissions={"can_manage_inventory": True})
    db.add(role)
    await db.flush()
    email = f"limited-{uuid.uuid4().hex[:6]}@t.dev"
    emp = Employee(org_id=org_id, name="Limited", email=email,
                   password_hash=hash_password("pw-123456789"), active=True, role_id=role.id)
    db.add(emp)
    await db.commit()
    tok = (await client.post("/api/auth/token", data={"username": email, "password": "pw-123456789"})).json()
    H = {"Authorization": f"Bearer {tok['access_token']}"}
    r = await client.post(f"/api/settings/warehouses/{wh['id']}/access", json={
        "employee_id": emp.id, "actions": ["writeoff"], "explicit": True}, headers=tenant)
    assert r.status_code == 200, r.text
    body = {"part_id": pid, "location_id": loc, "unit_cost": 5, "qty": 1}
    # the reference route refuses a post from this employee …
    denied = await client.post(f"/api/inventory/parts/{pid}/adjust", headers=H,
                               json={"location_id": loc, "qty": 1, "movement_type": "receive", "unit_cost": 5})
    assert denied.status_code == 403, denied.text
    # … and so must the batch route
    r = await client.post("/api/bins/batches", json=body, headers=H)
    assert r.status_code == 403, r.text
    assert await _sum(db, StockLevel.qty_available, pid) == 0
    assert await _sum(db, StockItemBatch.qty_received, pid) == 0
    # the same body from someone allowed to post still lands
    r = await client.post("/api/bins/batches", json=body, headers=tenant)
    assert r.status_code == 201, r.text


@_PG
@pytest.mark.asyncio
async def test_concurrent_batches_each_return_their_own_batch_id(client, tenant, db, monkeypatch):
    """Independent review: the response's `batch_id` was "the part's newest layer by received_at",
    which is another request's layer when that request commits in between. Two receipts of one
    product into two bins at once must each answer with the layer they created.

    The window is forced: the first receipt pauses between posting its layer and answering, so
    the second one lands and commits in the gap (both at the product's standing cost, so neither
    touches the Part row and neither waits for the other)."""
    from backend.services import stock_posting
    loc = await _loc(client, tenant)
    pid = await _part(client, tenant)
    r = await client.post(f"/api/inventory/parts/{pid}/adjust", headers=tenant,
                          json={"location_id": loc, "qty": 1, "movement_type": "receive", "unit_cost": 10})
    assert r.status_code == 200, r.text          # standing average 10: a receipt at 10 leaves the Part row alone
    bins = []
    for name in ("A1", "A2"):
        r = await client.post("/api/bins", json={"location_id": loc, "name": f"{name}-{uuid.uuid4().hex[:4]}"},
                              headers=tenant)
        assert r.status_code == 201, r.text
        bins.append(r.json()["id"])
    orig = stock_posting.apply_receipt_line
    calls = []

    async def paused(*a, **kw):
        res = await orig(*a, **kw)
        calls.append(kw.get("qty"))
        if len(calls) == 1:
            await asyncio.sleep(0.4)      # let the other receipt post its layer and commit
        return res
    monkeypatch.setattr(stock_posting, "apply_receipt_line", paused)
    r1, r2 = await asyncio.gather(
        client.post("/api/bins/batches", headers=tenant, json={
            "part_id": pid, "location_id": loc, "bin_id": bins[0], "unit_cost": 10, "qty": 2}),
        client.post("/api/bins/batches", headers=tenant, json={
            "part_id": pid, "location_id": loc, "bin_id": bins[1], "unit_cost": 10, "qty": 3}))
    assert (r1.status_code, r2.status_code) == (201, 201), (r1.text, r2.text)
    ids = (r1.json()["batch_id"], r2.json()["batch_id"])
    assert ids[0] != ids[1], ids
    for bid, qty in zip(ids, (2, 3)):
        layer = (await db.execute(select(StockItemBatch).where(StockItemBatch.id == bid))).scalar_one()
        assert float(layer.qty_received) == qty, (bid, float(layer.qty_received), qty)
    assert await _sum(db, StockLevel.qty_available, pid) == 6
