"""Copying a job leaves its gift-card, coupon and loyalty discount lines behind.

Those lines stand for value the customer spent on the ORIGINAL job (a card balance, a coupon use,
points); the copy never took any of it. Copied, the discount applied twice, and deleting the copy's
line gave back what it never took: the card was credited money it was never charged (found while
widening the nightly reconcile for audit DB-09, where coupon uses can't be re-derived because of it).
"""
import pytest
from sqlalchemy import select

from backend.models import GiftCard, OrderLine


@pytest.mark.asyncio
async def test_a_copied_job_does_not_carry_the_gift_card_spend(client, tenant, db):
    cid = (await client.post("/api/clients", json={"name": "Dup"}, headers=tenant)).json()["id"]
    code = (await client.post("/api/giftcards", json={"value": 60, "client_id": cid}, headers=tenant)).json()["code"]
    r = await client.post("/api/orders", headers=tenant, json={
        "client_id": cid, "complaint": "job",
        "lines": [{"line_type": "labour", "description": "Repair", "qty": 1, "unit_price": 100}]})
    oid = r.json()["id"]
    r = await client.post(f"/api/orders/{oid}/redeem-giftcard", json={"code": code, "amount": 25}, headers=tenant)
    assert r.status_code in (200, 201), r.text

    r = await client.post(f"/api/orders/{oid}/duplicate", headers=tenant)
    assert r.status_code == 201, r.text
    dup = r.json()["id"]
    db.expire_all()
    lines = (await db.execute(select(OrderLine).where(OrderLine.order_id == dup))).scalars().all()
    assert [ln.description for ln in lines] == ["Repair"]
    from backend.models import Order
    assert float((await db.execute(select(Order.total).where(Order.id == dup))).scalar_one()) == 100
    bal = float((await db.execute(select(GiftCard.balance).where(GiftCard.code == code))).scalar_one())
    assert bal == 35

    from backend.services import reconcile
    org = (await client.get("/api/auth/me", headers=tenant)).json()["org_id"]
    assert await reconcile.find_mismatches(db, org_id=org) == []
