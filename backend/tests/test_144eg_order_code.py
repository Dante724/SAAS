"""Build 144eg — Order code (barcode/QR) set on the order, shared with the
{Barcode}/{QR Code} print variables and the /by-barcode scanner lookup."""
import uuid
import pytest


async def _register(client):
    email = f"a-{uuid.uuid4().hex[:8]}@t.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Co", "name": "Admin", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _order(client, H):
    loc = (await client.get("/api/staff/locations", headers=H)).json()
    loc_id = (loc[0]["id"] if isinstance(loc, list) else loc["items"][0]["id"])
    return (await client.post("/api/orders", json={"location_id": loc_id, "complaint": "x"}, headers=H)).json()


@pytest.mark.asyncio
async def test_set_code_persists_and_scanner_resolves(client):
    H = await _register(client)
    o = await _order(client, H)
    r = await client.patch(f"/api/orders/{o['id']}/code", json={"barcode": "WO-EXPLICIT-9"}, headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["barcode"] == "WO-EXPLICIT-9"
    # scanner lookup resolves the explicit code to this order
    hit = await client.get("/api/documents/by-barcode/WO-EXPLICIT-9", headers=H)
    assert hit.status_code == 200, hit.text
    assert hit.json()["id"] == o["id"]
    # the order number still resolves too
    hit2 = await client.get(f"/api/documents/by-barcode/{o['order_number']}", headers=H)
    assert hit2.status_code == 200


@pytest.mark.asyncio
async def test_empty_code_regenerates_a_unique_one(client):
    # 144ei — a work order is never left codeless; clearing issues a fresh unique code.
    H = await _register(client)
    o = await _order(client, H)
    await client.patch(f"/api/orders/{o['id']}/code", json={"barcode": "ABC"}, headers=H)
    r = await client.patch(f"/api/orders/{o['id']}/code", json={"barcode": ""}, headers=H)
    assert r.status_code == 200
    assert r.json()["barcode"] and r.json()["barcode"] != "ABC"
    detail = (await client.get(f"/api/orders/{o['id']}", headers=H)).json()
    assert detail["barcode"]


@pytest.mark.asyncio
async def test_set_code_is_org_scoped(client):
    H1 = await _register(client)
    o = await _order(client, H1)
    H2 = await _register(client)
    r = await client.patch(f"/api/orders/{o['id']}/code", json={"barcode": "X"}, headers=H2)
    assert r.status_code == 404
