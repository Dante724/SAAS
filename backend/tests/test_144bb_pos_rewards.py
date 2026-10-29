"""144bb — POS (over-the-counter sale) rewards: coupon / gift card / loyalty fold into the
sale discount and decrement their ledgers, mirroring the order-based engage.py rules."""
import pytest
from sqlalchemy import select


def _svc_line(price):
    return {"kind": "service", "ref_id": None, "description": "Diagnostic", "qty": 1, "unit_price": price}


@pytest.mark.asyncio
async def test_pos_coupon_reduces_total(client, tenant):
    await client.post("/api/coupons", json={
        "code": "POS10", "kind": "pct", "value": 10, "min_total": 0, "max_uses": 5}, headers=tenant)
    r = await client.post("/api/sales", json={
        "lines": [_svc_line(100)], "coupon_code": "pos10"}, headers=tenant)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["total"] == pytest.approx(90.0)          # 100 - 10%
    assert body["redeemed"]["coupon"] == pytest.approx(10.0)


@pytest.mark.asyncio
async def test_pos_gift_card_decrements_balance(client, tenant):
    cid = (await client.post("/api/clients", json={"name": "GC Holder"}, headers=tenant)).json()["id"]
    g = (await client.post("/api/giftcards", json={"value": 30, "client_id": cid}, headers=tenant)).json()
    code = g["code"]
    r = await client.post("/api/sales", json={
        "lines": [_svc_line(50)], "gift_card_code": code}, headers=tenant)
    assert r.status_code == 201, r.text
    assert r.json()["total"] == pytest.approx(50.0)       # B5: the card is a tender, not a discount
    assert r.json()["due"] == pytest.approx(20.0)         # 50 - 30 card
    assert r.json()["redeemed"]["gift_card"] == pytest.approx(30.0)
    # card drained to 0
    assert (await client.get(f"/api/giftcards/{code}", headers=tenant)).json()["balance"] == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_pos_loyalty_redeem(client, tenant, db):
    from backend.models import Client as ClientM
    from backend.models import LoyaltyLedger, Organisation
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    org = (await db.execute(select(Organisation).where(Organisation.id == me["org_id"]))).scalar_one()
    org.loyalty_point_value = 0.05
    cl = ClientM(org_id=me["org_id"], name="Points Pat")
    db.add(cl)
    await db.flush()
    db.add(LoyaltyLedger(org_id=me["org_id"], client_id=cl.id, points=400, ref_type="seed", note="t"))
    await db.commit()
    await db.refresh(cl)

    r = await client.post("/api/sales", json={
        "client_id": cl.id, "lines": [_svc_line(100)], "redeem_points": 200}, headers=tenant)
    assert r.status_code == 201, r.text
    assert r.json()["total"] == pytest.approx(90.0)       # 200 * 0.05 = 10 off
    assert r.json()["redeemed"]["loyalty"] == pytest.approx(10.0)
    # remaining balance 200
    bal = float((await db.execute(select(__import__("sqlalchemy").func.coalesce(
        __import__("sqlalchemy").func.sum(LoyaltyLedger.points), 0.0)).where(
        LoyaltyLedger.client_id == cl.id))).scalar_one())
    assert bal == pytest.approx(200.0)


@pytest.mark.asyncio
async def test_pos_loyalty_needs_client(client, tenant):
    r = await client.post("/api/sales", json={
        "lines": [_svc_line(100)], "redeem_points": 50}, headers=tenant)
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_pos_plain_sale_unchanged(client, tenant):
    r = await client.post("/api/sales", json={"lines": [_svc_line(75)]}, headers=tenant)
    assert r.status_code == 201, r.text
    assert r.json()["total"] == pytest.approx(75.0)
    assert r.json()["redeemed"] == {}


@pytest.mark.asyncio
async def test_pos_sale_when_seller_has_no_location(client, tenant, db):
    """144be regression: a seller with no home location (e.g. the org owner) must still be able
    to ring a sale — create_sale falls back to the org's first location instead of 500-ing on the
    NOT NULL sales.location_id."""
    from backend.models import Employee
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    emp = (await db.execute(select(Employee).where(Employee.id == me["id"]))).scalar_one()
    emp.location_id = None
    await db.commit()
    r = await client.post("/api/sales", json={"lines": [_svc_line(40)]}, headers=tenant)
    assert r.status_code == 201, r.text
    assert r.json()["total"] == pytest.approx(40.0)
