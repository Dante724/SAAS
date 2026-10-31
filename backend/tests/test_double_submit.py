"""Double clicks and two people on one job must move stock and money once (QA multi-shop run,
31 Oct 2026). Each race is replayed deterministically: session A reads the record, session B
does the same action and commits, then A acts on its stale copy — exactly what two requests
in flight at once do."""
import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from backend.models import Order, StockMovement
from backend.tests import kit_helpers as K

P = {"line_type": "part", "kind": "product", "description": "Part", "unit_price": 20}


def _other(db):
    return async_sessionmaker(db.bind, expire_on_commit=False)


async def _estimate_with_part(client, h, qty=2, stock=10):
    await client.get("/api/statuses?is_estimate=true", headers=h)   # seed estimate statuses
    loc = await K.loc(client, h)
    pid = await K.part(client, h, loc, "Screen", 50, 10, qty=stock)
    oid, cid = await K.job(client, h, loc, estimate=True)
    await K.add_line(client, h, oid, ref_id=pid, qty=qty, **P)
    return oid, pid, cid


async def _uses(db, oid):
    db.expire_all()
    return (await db.execute(select(func.count()).select_from(StockMovement).where(
        StockMovement.ref_id == oid, StockMovement.movement_type == "use"))).scalar_one()


# ── 1. estimate → work order converts once ─────────────────────────────────────────────────────
class _Emp:
    id = None

    def __init__(self, org_id):
        self.org_id = org_id


@pytest.mark.asyncio
async def test_convert_twice_from_stale_copies_converts_once(client, tenant, db):
    from backend.routers.orders import _convert_estimate
    oid, pid, _ = await _estimate_with_part(client, tenant)
    stale = (await db.execute(select(Order).where(Order.id == oid))).scalar_one()   # A: still an estimate
    async with _other(db)() as b:
        est = (await b.execute(select(Order).where(Order.id == oid))).scalar_one()
        assert await _convert_estimate(b, _Emp(est.org_id), est) == oid
    number = (await client.get(f"/api/orders/{oid}", headers=tenant)).json()["order_number"]
    assert await _convert_estimate(db, _Emp(stale.org_id), stale) is None          # A lost the race
    await db.rollback()
    assert await K.stock(client, tenant, pid) == 8                                 # deducted once
    assert await _uses(db, oid) == 1
    assert (await client.get(f"/api/orders/{oid}", headers=tenant)).json()["order_number"] == number
    nxt = await client.post("/api/orders", headers=tenant, json={
        "client_id": None, "location_id": await K.loc(client, tenant), "complaint": "x", "lines": []})
    assert int(nxt.json()["order_number"][3:]) == int(number[3:]) + 1             # no WO number burned


@pytest.mark.asyncio
async def test_convert_endpoint_on_already_converted_is_rejected(client, tenant):
    oid, pid, _ = await _estimate_with_part(client, tenant)
    assert (await client.post(f"/api/orders/{oid}/convert", headers=tenant)).status_code == 200
    r = await client.post(f"/api/orders/{oid}/convert", headers=tenant)
    assert r.status_code in (400, 409), r.text
    assert await K.stock(client, tenant, pid) == 8


@pytest.mark.asyncio
async def test_double_customer_accept_converts_once(client, tenant, db):
    from backend.routers.public import _decide_estimate
    oid, pid, _ = await _estimate_with_part(client, tenant)
    stale = (await db.execute(select(Order).where(Order.id == oid))).scalar_one()
    async with _other(db)() as b:
        o = (await b.execute(select(Order).where(Order.id == oid))).scalar_one()
        assert await _decide_estimate(b, o, "accept", None, "Bob") == oid
        await b.commit()
    with pytest.raises(HTTPException) as e:                 # second tap: refused, nothing moves
        await _decide_estimate(db, stale, "accept", None, "Bob")
    assert e.value.status_code == 409
    await db.rollback()
    assert await K.stock(client, tenant, pid) == 8
    assert await _uses(db, oid) == 1
    o = await K.order(client, tenant, oid)
    assert o["is_estimate"] is False


@pytest.mark.asyncio
async def test_accept_after_staff_convert_does_not_convert_again(client, tenant, db):
    from backend.routers.public import _decide_estimate
    oid, pid, _ = await _estimate_with_part(client, tenant)
    stale = (await db.execute(select(Order).where(Order.id == oid))).scalar_one()
    assert (await client.post(f"/api/orders/{oid}/convert", headers=tenant)).status_code == 200
    with pytest.raises(HTTPException) as e:
        await _decide_estimate(db, stale, "accept", None, "Bob")
    assert e.value.status_code == 409
    await db.rollback()
    assert await K.stock(client, tenant, pid) == 8
    assert await _uses(db, oid) == 1


@pytest.mark.asyncio
async def test_declined_estimate_cannot_be_accepted_later(client, tenant):
    oid, pid, _ = await _estimate_with_part(client, tenant)
    tok = (await K.order(client, tenant, oid))["public_token"]
    r = await client.post(f"/api/public/track/{tok}/respond", json={"decision": "decline", "name": "Bob"})
    assert r.status_code == 200, r.text
    r = await client.post(f"/api/public/track/{tok}/respond", json={"decision": "accept", "name": "Bob"})
    assert r.status_code == 409, r.text
    o = await K.order(client, tenant, oid)
    assert o["is_estimate"] is True and await K.stock(client, tenant, pid) == 10
