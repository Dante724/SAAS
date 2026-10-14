"""Build 144dw — send invoice by email (PDF attach) + payer picker (bill-to)."""
import uuid
import pytest


async def _register(client):
    email = f"a-{uuid.uuid4().hex[:8]}@t.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Co", "name": "Admin", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _client(client, H, name, email=None):
    return (await client.post("/api/clients", json={"name": name, "email": email}, headers=H)).json()


async def _order(client, H):
    loc = (await client.get("/api/staff/locations", headers=H)).json()
    loc_id = (loc[0]["id"] if isinstance(loc, list) else loc["items"][0]["id"])
    o = (await client.post("/api/orders", json={"location_id": loc_id, "complaint": "x"}, headers=H)).json()
    await client.post(f"/api/orders/{o['id']}/lines", json={
        "line_type": "labour", "kind": "service", "description": "Fix", "unit_price": 50}, headers=H)
    return o


# ── payer picker ──────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_set_and_reset_payer(client):
    H = await _register(client)
    cust = await _client(client, H, "Customer", "cust@x.dev")
    payer = await _client(client, H, "Employer Ltd", "ap@employer.dev")
    o = await _order(client, H)
    inv = (await client.post("/api/finance/invoices", json={
        "order_id": o["id"], "client_id": cust["id"], "subtotal": 50}, headers=H)).json()
    # set a different payer
    body = (await client.patch(f"/api/finance/invoices/{inv['id']}",
            json={"payer_client_id": payer["id"]}, headers=H)).json()
    assert body["payer"]["id"] == payer["id"]
    assert body["customer"]["id"] == cust["id"]
    # reset payer
    body2 = (await client.patch(f"/api/finance/invoices/{inv['id']}",
             json={"payer_client_id": None}, headers=H)).json()
    assert body2["payer"] is None


@pytest.mark.asyncio
async def test_payer_must_be_in_org(client):
    H1 = await _register(client)
    o = await _order(client, H1)
    inv = (await client.post("/api/finance/invoices", json={"order_id": o["id"], "subtotal": 50}, headers=H1)).json()
    H2 = await _register(client)
    foreign = await _client(client, H2, "Foreign")
    r = await client.patch(f"/api/finance/invoices/{inv['id']}",
                           json={"payer_client_id": foreign["id"]}, headers=H1)
    assert r.status_code == 404


# ── send by email ─────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_send_invoice_simulated_when_no_connection(client):
    H = await _register(client)
    cust = await _client(client, H, "Payer", "pay@x.dev")
    o = await _order(client, H)
    inv = (await client.post("/api/finance/invoices", json={
        "order_id": o["id"], "client_id": cust["id"], "subtotal": 50}, headers=H)).json()
    r = await client.post(f"/api/finance/invoices/{inv['id']}/send", json={}, headers=H)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["status"] == "simulated"       # no email account connected in tests
    assert body["recipient"] == "pay@x.dev"     # pulled from the client on file


@pytest.mark.asyncio
async def test_send_invoice_needs_a_recipient(client):
    H = await _register(client)
    cust = await _client(client, H, "No Email")   # no email on file
    o = await _order(client, H)
    inv = (await client.post("/api/finance/invoices", json={
        "order_id": o["id"], "client_id": cust["id"], "subtotal": 50}, headers=H)).json()
    r = await client.post(f"/api/finance/invoices/{inv['id']}/send", json={}, headers=H)
    assert r.status_code == 422
    # explicit override works
    r2 = await client.post(f"/api/finance/invoices/{inv['id']}/send",
                           json={"to": "manual@x.dev"}, headers=H)
    assert r2.status_code == 200 and r2.json()["recipient"] == "manual@x.dev"


@pytest.mark.asyncio
async def test_send_invoice_org_scoped(client):
    H1 = await _register(client)
    o = await _order(client, H1)
    inv = (await client.post("/api/finance/invoices", json={"order_id": o["id"], "subtotal": 50}, headers=H1)).json()
    H2 = await _register(client)
    r = await client.post(f"/api/finance/invoices/{inv['id']}/send", json={"to": "x@x.dev"}, headers=H2)
    assert r.status_code == 404
