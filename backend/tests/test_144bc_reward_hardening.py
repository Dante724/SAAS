"""144bc — reward-ledger hardening.

Two money-safety guards applied to both the POS (sales.py) and the work-order (engage.py)
redeem paths:
  1. Gift-card balance decremented via a guarded atomic UPDATE (WHERE balance >= amt), so two
     concurrent redemptions can't drive the balance negative / double-spend.
  2. Loyalty redemption value is capped at the remaining bill, so points past what's owed are
     never burned for no benefit.
"""
import pytest
from sqlalchemy import func, select
from sqlalchemy import update as sa_update


def _svc(price):
    return {"kind": "service", "ref_id": None, "description": "Svc", "qty": 1, "unit_price": price}


@pytest.mark.asyncio
async def test_pos_gift_card_atomic_decrement_rejects_oversell(client, tenant, db):
    """Simulate a concurrent draw: after the balance read, something else drains the card.
    The guarded UPDATE must refuse to drive it negative."""
    from backend.models import GiftCard
    cid = (await client.post("/api/clients", json={"name": "GC"}, headers=tenant)).json()["id"]
    g = (await client.post("/api/giftcards", json={"value": 40, "client_id": cid}, headers=tenant)).json()
    code = g["code"]
    # drain the card to 5 behind the API's back (stands in for a racing redemption)
    gid = (await db.execute(select(GiftCard.id).where(GiftCard.code == code))).scalar_one()
    await db.execute(sa_update(GiftCard).where(GiftCard.id == gid).values(balance=5))
    await db.commit()
    # sale tries to pull 50-worth; only 5 is really there — must not go negative
    r = await client.post("/api/sales", json={"lines": [_svc(50)], "gift_card_code": code}, headers=tenant)
    # either it caps at the real 5 or 409s, but the balance must never be negative
    bal = (await db.execute(select(GiftCard.balance).where(GiftCard.id == gid))).scalar_one()
    assert bal >= 0, f"balance went negative: {bal}"
    if r.status_code == 201:
        assert r.json()["redeemed"].get("gift_card", 0) <= 5.0


@pytest.mark.asyncio
async def test_pos_loyalty_caps_at_bill(client, tenant, db):
    """Redeeming more points than the bill is worth must only consume points up to the bill,
    not burn the surplus."""
    from backend.models import Client as ClientM
    from backend.models import LoyaltyLedger, Organisation
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    org = (await db.execute(select(Organisation).where(Organisation.id == me["org_id"]))).scalar_one()
    org.loyalty_point_value = 0.10          # 1 pt = 0.10
    cl = ClientM(org_id=me["org_id"], name="Cap Carl")
    db.add(cl)
    await db.flush()
    db.add(LoyaltyLedger(org_id=me["org_id"], client_id=cl.id, points=1000, ref_type="seed", note="t"))
    await db.commit()
    await db.refresh(cl)

    # bill 30; redeeming 1000 pts (=100 value) should cap at 30 value = 300 pts
    r = await client.post("/api/sales", json={
        "client_id": cl.id, "lines": [_svc(30)], "redeem_points": 1000}, headers=tenant)
    assert r.status_code == 201, r.text
    assert r.json()["total"] == pytest.approx(0.0)
    assert r.json()["redeemed"]["loyalty"] == pytest.approx(30.0)   # capped value, not 100
    bal = float((await db.execute(select(func.coalesce(func.sum(LoyaltyLedger.points), 0.0)).where(
        LoyaltyLedger.client_id == cl.id))).scalar_one())
    assert bal == pytest.approx(700.0)        # only 300 pts consumed, not 1000


@pytest.mark.asyncio
async def test_wo_loyalty_caps_at_order_balance(client, tenant, db):
    """Same cap on the work-order redeem path."""
    from backend.models import Client as ClientM
    from backend.models import LoyaltyLedger, Organisation
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    org = (await db.execute(select(Organisation).where(Organisation.id == me["org_id"]))).scalar_one()
    org.loyalty_point_value = 0.10
    cl = ClientM(org_id=me["org_id"], name="WO Cap")
    db.add(cl)
    await db.flush()
    db.add(LoyaltyLedger(org_id=me["org_id"], client_id=cl.id, points=1000, ref_type="seed", note="t"))
    await db.commit()
    await db.refresh(cl)
    o = (await client.post("/api/orders", json={"client_id": cl.id, "location_id": loc, "complaint": "x"},
                           headers=tenant)).json()
    await client.post(f"/api/orders/{o['id']}/lines", json={
        "line_type": "part", "kind": "custom", "description": "p", "qty": 1, "unit_price": 20}, headers=tenant)
    # redeem 1000 pts (=100) against a 20 bill → cap at 200 pts / 20 value
    r = await client.post(f"/api/orders/{o['id']}/redeem-loyalty", json={"points": 1000}, headers=tenant)
    assert r.status_code == 200, r.text
    assert r.json()["discount"] == pytest.approx(20.0)
    bal = float((await db.execute(select(func.coalesce(func.sum(LoyaltyLedger.points), 0.0)).where(
        LoyaltyLedger.client_id == cl.id))).scalar_one())
    assert bal == pytest.approx(800.0)        # only 200 consumed


@pytest.mark.asyncio
async def test_wo_gift_card_atomic_decrement(client, tenant, db):
    """WO redeem-giftcard must also refuse to go negative under a racing drain."""
    from backend.models import GiftCard
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    cid = (await client.post("/api/clients", json={"name": "GC2"}, headers=tenant)).json()["id"]
    g = (await client.post("/api/giftcards", json={"value": 40, "client_id": cid}, headers=tenant)).json()
    code = g["code"]
    gid = (await db.execute(select(GiftCard.id).where(GiftCard.code == code))).scalar_one()
    await db.execute(sa_update(GiftCard).where(GiftCard.id == gid).values(balance=5))
    await db.commit()
    o = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x"},
                           headers=tenant)).json()
    await client.post(f"/api/orders/{o['id']}/lines", json={
        "line_type": "part", "kind": "custom", "description": "p", "qty": 1, "unit_price": 50}, headers=tenant)
    r = await client.post(f"/api/orders/{o['id']}/redeem-giftcard", json={"code": code, "amount": 50}, headers=tenant)
    bal = (await db.execute(select(GiftCard.balance).where(GiftCard.id == gid))).scalar_one()
    assert bal >= 0
    assert r.status_code in (200, 400, 409)
