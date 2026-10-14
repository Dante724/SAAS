"""Build 144dt — the Order-display feature made functional.

An invoice's display_mode was stored but never applied. Now GET
/finance/invoices/{id} returns resolved `lines`:
  detailed   -> every order line
  simplified -> one line per source order
  one_line   -> a single line labelled with the order
"""
import uuid
import pytest


async def _register(client):
    email = f"a-{uuid.uuid4().hex[:8]}@t.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Co", "name": "Admin", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _order_with_lines(client, H):
    loc = (await client.get("/api/staff/locations", headers=H)).json()
    loc_id = (loc[0]["id"] if isinstance(loc, list) else loc["items"][0]["id"])
    o = (await client.post("/api/orders", json={"location_id": loc_id, "complaint": "Screen"}, headers=H)).json()
    for d, p in (("Diag", 40), ("Screen part", 80)):
        await client.post(f"/api/orders/{o['id']}/lines", json={
            "line_type": "labour", "kind": "service", "description": d, "unit_price": p}, headers=H)
    return o


async def _invoice(client, H, order_id, mode):
    r = await client.post("/api/finance/invoices", json={
        "order_id": order_id, "subtotal": 120, "tax": 0, "display_mode": mode}, headers=H)
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.mark.asyncio
async def test_detailed_mode_returns_every_line(client):
    H = await _register(client)
    o = await _order_with_lines(client, H)
    inv_id = await _invoice(client, H, o["id"], "detailed")
    body = (await client.get(f"/api/finance/invoices/{inv_id}", headers=H)).json()
    assert body["display_mode"] == "detailed"
    assert len(body["lines"]) == 2
    assert {l["description"] for l in body["lines"]} == {"Diag", "Screen part"}


@pytest.mark.asyncio
async def test_simplified_mode_collapses_to_one_order_line(client):
    H = await _register(client)
    o = await _order_with_lines(client, H)
    inv_id = await _invoice(client, H, o["id"], "simplified")
    body = (await client.get(f"/api/finance/invoices/{inv_id}", headers=H)).json()
    assert body["display_mode"] == "simplified"
    assert len(body["lines"]) == 1
    assert o["order_number"] in body["lines"][0]["description"]


@pytest.mark.asyncio
async def test_one_line_mode_is_a_single_labelled_line(client):
    H = await _register(client)
    o = await _order_with_lines(client, H)
    inv_id = await _invoice(client, H, o["id"], "one_line")
    body = (await client.get(f"/api/finance/invoices/{inv_id}", headers=H)).json()
    assert body["display_mode"] == "one_line"
    assert len(body["lines"]) == 1
    assert body["lines"][0]["total"] == body["subtotal"]


@pytest.mark.asyncio
async def test_patch_switches_mode_without_rewriting_lines(client):
    H = await _register(client)
    o = await _order_with_lines(client, H)
    inv_id = await _invoice(client, H, o["id"], "detailed")
    # switch to one_line via PATCH
    body = (await client.patch(f"/api/finance/invoices/{inv_id}",
            json={"display_mode": "one_line"}, headers=H)).json()
    assert body["display_mode"] == "one_line"
    assert len(body["lines"]) == 1
    # switch back to detailed — every line comes back (nothing was destroyed)
    body2 = (await client.patch(f"/api/finance/invoices/{inv_id}",
             json={"display_mode": "detailed"}, headers=H)).json()
    assert len(body2["lines"]) == 2


@pytest.mark.asyncio
async def test_get_invoice_is_org_scoped(client):
    H1 = await _register(client)
    o = await _order_with_lines(client, H1)
    inv_id = await _invoice(client, H1, o["id"], "detailed")
    H2 = await _register(client)
    r = await client.get(f"/api/finance/invoices/{inv_id}", headers=H2)
    assert r.status_code == 404
