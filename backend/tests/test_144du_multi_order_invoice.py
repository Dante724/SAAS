"""Build 144du — an invoice can bill several work orders (Orderry parity).

Verifies fan-out across linked orders for all three display modes, plus the
attach/detach endpoints and their org scoping.
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
async def test_create_seeds_primary_order_link(client):
    H = await _register(client)
    o = await _order(client, H, "A", [("Diag", 40)])
    inv = (await client.post("/api/finance/invoices", json={
        "order_id": o["id"], "subtotal": 40, "display_mode": "detailed"}, headers=H)).json()
    body = (await client.get(f"/api/finance/invoices/{inv['id']}", headers=H)).json()
    assert [x["id"] for x in body["orders"]] == [o["id"]]
    assert len(body["lines"]) == 1


@pytest.mark.asyncio
async def test_attach_second_order_fans_out_all_modes(client):
    H = await _register(client)
    o1 = await _order(client, H, "Screen", [("Diag", 40), ("Part", 80)])
    o2 = await _order(client, H, "Battery", [("Cell", 55)])
    inv = (await client.post("/api/finance/invoices", json={
        "order_id": o1["id"], "subtotal": 175, "display_mode": "detailed"}, headers=H)).json()
    det = (await client.post(f"/api/finance/invoices/{inv['id']}/orders",
           json={"order_id": o2["id"]}, headers=H)).json()
    assert len(det["orders"]) == 2
    # detailed = every line across both orders (2 + 1 = 3)
    assert len(det["lines"]) == 3
    # simplified = one line per order (2)
    simp = (await client.patch(f"/api/finance/invoices/{inv['id']}",
            json={"display_mode": "simplified"}, headers=H)).json()
    assert len(simp["lines"]) == 2
    assert o1["order_number"] in simp["lines"][0]["description"]
    # one_line = single line, labelled with first order + "more"
    one = (await client.patch(f"/api/finance/invoices/{inv['id']}",
           json={"display_mode": "one_line"}, headers=H)).json()
    assert len(one["lines"]) == 1
    assert "more" in one["lines"][0]["description"]


@pytest.mark.asyncio
async def test_detach_order_removes_its_lines(client):
    H = await _register(client)
    o1 = await _order(client, H, "A", [("L1", 10)])
    o2 = await _order(client, H, "B", [("L2", 20)])
    inv = (await client.post("/api/finance/invoices", json={
        "order_id": o1["id"], "subtotal": 30, "display_mode": "detailed"}, headers=H)).json()
    await client.post(f"/api/finance/invoices/{inv['id']}/orders", json={"order_id": o2["id"]}, headers=H)
    await client.delete(f"/api/finance/invoices/{inv['id']}/orders/{o2['id']}", headers=H)
    body = (await client.get(f"/api/finance/invoices/{inv['id']}", headers=H)).json()
    assert [x["id"] for x in body["orders"]] == [o1["id"]]
    assert len(body["lines"]) == 1


@pytest.mark.asyncio
async def test_attach_is_org_scoped(client):
    H1 = await _register(client)
    o1 = await _order(client, H1, "A", [("L1", 10)])
    inv = (await client.post("/api/finance/invoices", json={"order_id": o1["id"], "subtotal": 10}, headers=H1)).json()
    H2 = await _register(client)
    r = await client.post(f"/api/finance/invoices/{inv['id']}/orders", json={"order_id": o1["id"]}, headers=H2)
    assert r.status_code == 404
