"""Build 144ds (Sprint 3) — commission by close outcome, invoice status on the
orders list, and approved-recs flowing onto the invoice."""
import uuid

import pytest
from sqlalchemy import select


async def _register(client):
    email = f"a-{uuid.uuid4().hex[:8]}@t.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Co", "name": "Admin", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    b = r.json()
    return {"Authorization": f"Bearer {b['access_token']}"}, b


async def _order(client, H):
    loc = (await client.get("/api/staff/locations", headers=H)).json()
    loc_id = (loc[0]["id"] if isinstance(loc, list) else loc["items"][0]["id"])
    return (await client.post("/api/orders", json={"location_id": loc_id, "complaint": "x"}, headers=H)).json()


@pytest.mark.asyncio
async def test_commission_close_outcome_events_available(client):
    H, _ = await _register(client)
    rules = (await client.get("/api/payroll/rules", headers=H)).json()
    assert "order_closed_success" in rules["events"]
    assert "order_closed_fail" in rules["events"]
    # a success-only rule is accepted
    r = await client.post("/api/payroll/rules", json={
        "name": "Win bonus", "event": "order_closed_success", "kind": "fixed", "amount": 5}, headers=H)
    assert r.status_code in (200, 201), r.text


@pytest.mark.asyncio
async def test_invoice_status_on_orders_list(client):
    H, _ = await _register(client)
    o = await _order(client, H)
    # not invoiced yet
    lst = (await client.get("/api/orders", headers=H)).json()
    row = next(x for x in lst["items"] if x["id"] == o["id"])
    assert row.get("invoice_status") is None
    # create an invoice → the order now shows a status
    await client.post("/api/finance/invoices", json={"order_id": o["id"], "subtotal": 50}, headers=H)
    lst2 = (await client.get("/api/orders", headers=H)).json()
    row2 = next(x for x in lst2["items"] if x["id"] == o["id"])
    assert row2.get("invoice_status") in ("draft", "issued", "paid")


# Note: "approved recs → invoice" is already covered end-to-end — customer approval on
# the portal (public.py) runs approve_recommendation(), which creates the OrderLine
# (tested in test_build144be_reply_and_gates). Those lines are in the order total, so
# they flow onto the invoice/auto-draft automatically. No new code needed here.
