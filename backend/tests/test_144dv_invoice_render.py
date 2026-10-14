"""Build 144dv — the invoice's Order-display mode drives BOTH the printed/PDF
invoice and the client-facing pay page, not just the staff detail modal.
"""
import uuid

import pytest


async def _register(client):
    email = f"a-{uuid.uuid4().hex[:8]}@t.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Co", "name": "Admin", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _order(client, H, complaint, lines):
    loc = (await client.get("/api/staff/locations", headers=H)).json()
    loc_id = (loc[0]["id"] if isinstance(loc, list) else loc["items"][0]["id"])
    o = (await client.post("/api/orders", json={"location_id": loc_id, "complaint": complaint}, headers=H)).json()
    for d, p in lines:
        await client.post(f"/api/orders/{o['id']}/lines", json={
            "line_type": "labour", "kind": "service", "description": d, "unit_price": p}, headers=H)
    return o


@pytest.mark.asyncio
async def test_printed_invoice_is_pdf(client):
    H = await _register(client)
    o = await _order(client, H, "Screen", [("Diag", 40), ("Part", 80)])
    inv = (await client.post("/api/finance/invoices", json={
        "order_id": o["id"], "subtotal": 120, "display_mode": "detailed"}, headers=H)).json()
    r = await client.get(f"/api/print/invoices/{inv['id']}", headers=H)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf"
    assert r.content[:4] == b"%PDF"


@pytest.mark.asyncio
async def test_printed_invoice_org_scoped(client):
    H1 = await _register(client)
    o = await _order(client, H1, "A", [("L", 10)])
    inv = (await client.post("/api/finance/invoices", json={"order_id": o["id"], "subtotal": 10}, headers=H1)).json()
    H2 = await _register(client)
    r = await client.get(f"/api/print/invoices/{inv['id']}", headers=H2)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_pay_page_shows_display_lines(client):
    """Close an order -> auto-draft invoice -> payment link -> the public pay page
    exposes the invoice's resolved lines in whatever mode is set."""
    H = await _register(client)
    await client.put("/api/staff/company", json={"auto_draft_invoice_on_close": True}, headers=H)
    o = await _order(client, H, "Battery", [("Diag", 30), ("Cell", 90)])
    close = await client.patch(f"/api/orders/{o['id']}/status", json={"status": "done"}, headers=H)
    assert close.status_code == 200, close.text
    # set the invoice to one_line so the pay page collapses it
    lst = (await client.get("/api/finance/invoices", headers=H)).json()
    items = lst.get("items", lst) if isinstance(lst, dict) else lst
    inv = [i for i in items if i.get("order_id") == o["id"]][0]
    await client.patch(f"/api/finance/invoices/{inv['id']}", json={"display_mode": "one_line"}, headers=H)

    link = await client.post(f"/api/orders/{o['id']}/payment-link", json={}, headers=H)
    assert link.status_code == 201, link.text
    intent_id = link.json()["intent_id"]

    info = (await client.get(f"/api/public/pay/{intent_id}")).json()
    assert info["display_mode"] == "one_line"
    assert info["lines"] is not None and len(info["lines"]) == 1
    assert info["invoice_number"]

    # switch to detailed -> pay page now shows both lines
    await client.patch(f"/api/finance/invoices/{inv['id']}", json={"display_mode": "detailed"}, headers=H)
    info2 = (await client.get(f"/api/public/pay/{intent_id}")).json()
    assert len(info2["lines"]) == 2
