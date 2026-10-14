"""144cu — the public track link must be unique per work order and never repeat.
Guarantee = DB unique constraint on orders.public_token + collision-checked generation."""
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


async def _order(client, h, loc):
    cid = (await client.post("/api/clients", json={"name": "C"}, headers=h)).json()["id"]
    return (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x"},
                               headers=h)).json()


@pytest.mark.asyncio
async def test_every_order_gets_a_distinct_nonempty_token(client, tenant):
    loc = await _loc(client, tenant)
    toks = []
    for _ in range(12):
        o = await _order(client, tenant, loc)
        det = (await client.get(f"/api/orders/{o['id']}", headers=tenant)).json()
        toks.append(det["public_token"])
    assert all(toks), "every order must have a public token at birth"
    assert len(set(toks)) == len(toks), "tokens must be unique across orders"


@pytest.mark.asyncio
async def test_rotate_mints_a_new_unique_token(client, tenant):
    loc = await _loc(client, tenant)
    o = await _order(client, tenant, loc)
    t0 = (await client.get(f"/api/orders/{o['id']}", headers=tenant)).json()["public_token"]
    r = await client.post(f"/api/orders/{o['id']}/public-link/rotate", headers=tenant)
    assert r.status_code == 200
    t1 = r.json()["token"]
    assert t1 and t1 != t0


@pytest.mark.asyncio
async def test_duplicate_token_is_rejected_by_db(client, tenant, db):
    """The hard guarantee: two orders cannot share a public_token."""
    from backend.models import Order
    loc = await _loc(client, tenant)
    a = await _order(client, tenant, loc)
    b = await _order(client, tenant, loc)
    oa = (await db.execute(select(Order).where(Order.id == a["id"]))).scalar_one()
    ob = (await db.execute(select(Order).where(Order.id == b["id"]))).scalar_one()
    ob.public_token = oa.public_token  # force a collision
    with pytest.raises(IntegrityError):
        await db.commit()
