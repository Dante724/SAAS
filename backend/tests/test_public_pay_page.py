"""B12: the public pay page is branded with the shop asking for money, and a link that can't be
paid says so instead of offering a live "Pay $0.00" button."""
import uuid

import pytest
from sqlalchemy import select


async def _register(client, org_name):
    email = f"a-{uuid.uuid4().hex[:8]}@t.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": org_name, "name": "Admin", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _payment_link(client, H, price=80):
    loc = (await client.get("/api/staff/locations", headers=H)).json()
    loc_id = (loc[0]["id"] if isinstance(loc, list) else loc["items"][0]["id"])
    o = (await client.post("/api/orders", json={"location_id": loc_id, "complaint": "Screen"}, headers=H)).json()
    await client.post(f"/api/orders/{o['id']}/lines", json={
        "line_type": "labour", "kind": "service", "description": "Screen fit", "unit_price": price}, headers=H)
    link = await client.post(f"/api/orders/{o['id']}/payment-link", json={}, headers=H)
    assert link.status_code == 201, link.text
    return link.json()["intent_id"]


@pytest.mark.asyncio
async def test_pay_page_carries_the_shops_name_and_address(client):
    H = await _register(client, "Main Street Phone Fix")
    await client.put("/api/staff/company", json={"company_address": "12 Main St, Springfield"}, headers=H)
    intent_id = await _payment_link(client, H)
    info = (await client.get(f"/api/public/pay/{intent_id}")).json()
    assert info["shop_name"] == "Main Street Phone Fix"
    assert info["shop_address"] == "12 Main St, Springfield"
    assert info["payable"] is True
    assert info["amount"] > 0


@pytest.mark.asyncio
async def test_unknown_link_is_404(client):
    r = await client.get(f"/api/public/pay/{uuid.uuid4()}")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_cancelled_or_empty_link_is_not_payable(client, db):
    from backend.models import PaymentIntent
    H = await _register(client, "Corner Repairs")
    intent_id = await _payment_link(client, H)
    i = (await db.execute(select(PaymentIntent).where(PaymentIntent.id == intent_id))).scalar_one()
    i.status = "canceled"
    await db.commit()
    info = (await client.get(f"/api/public/pay/{intent_id}")).json()
    assert info["payable"] is False
    assert info["shop_name"] == "Corner Repairs"   # the "not valid" state still says who to call

    i.status, i.amount = "created", 0
    await db.commit()
    info = (await client.get(f"/api/public/pay/{intent_id}")).json()
    assert info["payable"] is False
