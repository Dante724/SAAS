"""Hardening council M13 (backend half): POST /orders/{id}/lines ignored Idempotency-Key, so a
retried "add a line" (a phone timeout that did reach the server, a double tap before the button
disabled, Tekly Go's queue replay) added the line twice: for a product, qty 2 and two units off
the shelf. orders.create and orders.finish already used services/idempotency.idem_begin; add_line
now does the same, so the retry gets the first answer back and nothing runs twice.
"""
import asyncio
import os
import uuid

import pytest
from sqlalchemy import select

from backend.models import OrderLine, StockLevel

pytestmark = pytest.mark.asyncio

_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL", "")
pg_only = pytest.mark.skipif(not _URL.startswith("postgresql"), reason="real parallel sessions need Postgres")


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


async def _order(client, h):
    cid = (await client.post("/api/clients", json={"name": "C", "phone": "+1"}, headers=h)).json()["id"]
    return (await client.post("/api/orders", json={"client_id": cid}, headers=h)).json()["id"]


async def _part_in_stock(client, h, qty=5):
    loc = await _loc(client, h)
    p = (await client.post("/api/inventory/parts", json={
        "name": "Screen", "sku": f"SCR-{uuid.uuid4().hex[:6]}", "cost_price": 5, "sell_price": 10}, headers=h)).json()
    r = await client.post(f"/api/inventory/parts/{p['id']}/adjust", json={
        "location_id": loc, "qty": qty, "movement_type": "receive"}, headers=h)
    assert r.status_code in (200, 201), r.text
    return p["id"], loc


async def test_retried_add_line_with_the_same_key_adds_once(client, tenant, db):
    pid, loc = await _part_in_stock(client, tenant)
    oid = await _order(client, tenant)
    key = {"Idempotency-Key": f"line-{uuid.uuid4()}"}
    body = {"description": "Screen", "kind": "product", "line_type": "part", "ref_id": pid, "qty": 1, "unit_price": 10}
    a = await client.post(f"/api/orders/{oid}/lines", json=body, headers={**tenant, **key})
    b = await client.post(f"/api/orders/{oid}/lines", json=body, headers={**tenant, **key})   # the retry
    assert (a.status_code, b.status_code) == (201, 201), (a.text, b.text)
    assert b.json() == a.json(), "the retry gets the first answer back"
    assert b.json().get("merged") is not True
    lines = (await db.execute(select(OrderLine).where(OrderLine.order_id == oid))).scalars().all()
    assert len(lines) == 1 and float(lines[0].qty) == 1, [float(ln.qty) for ln in lines]
    sl = (await db.execute(select(StockLevel).where(StockLevel.part_id == pid, StockLevel.location_id == loc))).scalars().first()
    assert sl.qty_available == 4, f"on hand {sl.qty_available} (was 5): the retry took stock again"


async def test_a_different_key_still_adds_again(client, tenant, db):
    """Two deliberate adds of the same product (two keys) keep merging into one line, qty 2."""
    pid, _ = await _part_in_stock(client, tenant)
    oid = await _order(client, tenant)
    body = {"description": "Screen", "kind": "product", "line_type": "part", "ref_id": pid, "qty": 1, "unit_price": 10}
    for _ in range(2):
        r = await client.post(f"/api/orders/{oid}/lines", json=body,
                              headers={**tenant, "Idempotency-Key": f"line-{uuid.uuid4()}"})
        assert r.status_code == 201, r.text
    lines = (await db.execute(select(OrderLine).where(OrderLine.order_id == oid))).scalars().all()
    assert len(lines) == 1 and float(lines[0].qty) == 2


async def test_no_key_is_unchanged(client, tenant, db):
    oid = await _order(client, tenant)
    body = {"line_type": "labour", "description": "Fix", "qty": 1, "unit_price": 100}
    for _ in range(2):
        assert (await client.post(f"/api/orders/{oid}/lines", json=body, headers=tenant)).status_code == 201
    lines = (await db.execute(select(OrderLine).where(OrderLine.order_id == oid))).scalars().all()
    assert len(lines) == 2


async def test_a_failed_add_releases_the_key_for_a_retry(client, tenant):
    oid = await _order(client, tenant)
    key = {"Idempotency-Key": f"line-{uuid.uuid4()}"}
    bad = {"line_type": "labour", "description": "Fix", "qty": 1, "unit_price": 100, "technician_id": "nobody"}
    r = await client.post(f"/api/orders/{oid}/lines", json=bad, headers={**tenant, **key})
    assert r.status_code >= 400, r.text
    good = {"line_type": "labour", "description": "Fix", "qty": 1, "unit_price": 100}
    r = await client.post(f"/api/orders/{oid}/lines", json=good, headers={**tenant, **key})
    assert r.status_code == 201, r.text


async def test_a_key_used_for_a_create_is_refused_on_add_line(client, tenant):
    cid = (await client.post("/api/clients", json={"name": "C", "phone": "+1"}, headers=tenant)).json()["id"]
    key = {"Idempotency-Key": f"mixed-{uuid.uuid4()}"}
    r = await client.post("/api/orders", json={"client_id": cid}, headers={**tenant, **key})
    assert r.status_code == 201, r.text
    oid = r.json()["id"]
    r = await client.post(f"/api/orders/{oid}/lines", json={
        "line_type": "labour", "description": "Fix", "qty": 1, "unit_price": 100}, headers={**tenant, **key})
    assert r.status_code == 422, r.text


@pg_only
async def test_a_double_tap_sent_at_once_adds_once(client, tenant, db):
    """Two requests with one key at the same moment (a double tap): one adds the line, the other
    gets the first answer or a 409 to retry; the stock moves once."""
    pid, loc = await _part_in_stock(client, tenant)
    oid = await _order(client, tenant)
    key = {"Idempotency-Key": f"line-{uuid.uuid4()}"}
    body = {"description": "Screen", "kind": "product", "line_type": "part", "ref_id": pid, "qty": 1, "unit_price": 10}
    rs = await asyncio.gather(*[client.post(f"/api/orders/{oid}/lines", json=body, headers={**tenant, **key}) for _ in range(2)])
    codes = sorted(r.status_code for r in rs)
    assert codes in ([201, 201], [201, 409]), [r.text for r in rs]
    lines = (await db.execute(select(OrderLine).where(OrderLine.order_id == oid))).scalars().all()
    assert len(lines) == 1 and float(lines[0].qty) == 1, [float(ln.qty) for ln in lines]
    sl = (await db.execute(select(StockLevel).where(StockLevel.part_id == pid, StockLevel.location_id == loc))).scalars().first()
    await db.refresh(sl)
    assert sl.qty_available == 4, f"on hand {sl.qty_available} (was 5)"


async def test_the_same_key_with_a_different_line_is_refused(client, tenant, db):
    """#99 binds a key to what was asked. A key reused for a DIFFERENT line (a client bug, or a key
    kept across two actions) must be refused (422), not answered with the first line while the
    second is silently never added."""
    pid, _loc_id = await _part_in_stock(client, tenant)
    oid = await _order(client, tenant)
    key = {"Idempotency-Key": f"line-{uuid.uuid4()}"}
    first = {"description": "Screen", "kind": "product", "line_type": "part", "ref_id": pid, "qty": 1, "unit_price": 10}
    other = {**first, "qty": 2}
    a = await client.post(f"/api/orders/{oid}/lines", json=first, headers={**tenant, **key})
    b = await client.post(f"/api/orders/{oid}/lines", json=other, headers={**tenant, **key})
    assert a.status_code == 201, a.text
    assert b.status_code == 422, f"{b.status_code} {b.text[:200]}"
    lines = (await db.execute(select(OrderLine).where(OrderLine.order_id == oid))).scalars().all()
    assert [float(ln.qty) for ln in lines] == [1.0]


async def test_the_same_key_on_another_job_is_refused(client, tenant):
    """The path is part of what was asked: the same key and body on a second job is a different request."""
    pid, _loc_id = await _part_in_stock(client, tenant)
    o1, o2 = await _order(client, tenant), await _order(client, tenant)
    key = {"Idempotency-Key": f"line-{uuid.uuid4()}"}
    body = {"description": "Screen", "kind": "product", "line_type": "part", "ref_id": pid, "qty": 1, "unit_price": 10}
    assert (await client.post(f"/api/orders/{o1}/lines", json=body, headers={**tenant, **key})).status_code == 201
    r = await client.post(f"/api/orders/{o2}/lines", json=body, headers={**tenant, **key})
    assert r.status_code == 422, f"{r.status_code} {r.text[:200]}"
