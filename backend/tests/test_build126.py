"""Build 126 — editable assets, client communication, and save-to-catalog custom lines."""
import pytest


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


async def _client(client, h, name="Asset Owner"):
    r = await client.post("/api/clients", json={"name": name, "phone": "+15557777"}, headers=h)
    return r.json()["id"]


# ── editable asset ─────────────────────────────────────────
@pytest.mark.asyncio
async def test_asset_edit_updates_fields(client, tenant):
    cid = await _client(client, tenant)
    a = (await client.post("/api/assets", json={
        "owner_type": "client", "client_id": cid, "asset_type": "Phone",
        "brand": "Apple", "model": "iPhone 14", "serial": "SN-1"}, headers=tenant)).json()
    aid = a["id"]

    r = await client.put(f"/api/assets/{aid}", json={
        "brand": "Samsung", "model": "Galaxy S24", "condition": "Good"}, headers=tenant)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["brand"] == "Samsung" and body["model"] == "Galaxy S24" and body["condition"] == "Good"

    got = (await client.get(f"/api/assets/{aid}", headers=tenant)).json()
    assert got["brand"] == "Samsung"
    assert "client_balance" in got            # balance shown inline


@pytest.mark.asyncio
async def test_asset_reassign_owner_to_company(client, tenant):
    cid = await _client(client, tenant)
    a = (await client.post("/api/assets", json={
        "owner_type": "client", "client_id": cid, "asset_type": "Tool", "serial": "T-1"}, headers=tenant)).json()
    r = await client.put(f"/api/assets/{a['id']}", json={"owner_type": "company"}, headers=tenant)
    assert r.status_code == 200, r.text
    assert r.json()["owner_type"] == "company" and r.json()["client_id"] is None


# ── client communication ───────────────────────────────────
@pytest.mark.asyncio
async def test_client_communication_send_and_list(client, tenant):
    cid = await _client(client, tenant, name="Chatty")
    r = await client.post(f"/api/clients/{cid}/communication",
                          json={"channel": "email", "body": "Your device is ready"}, headers=tenant)
    assert r.status_code == 201, r.text

    d = (await client.get(f"/api/clients/{cid}/communication", headers=tenant)).json()
    assert len(d["items"]) == 1
    m = d["items"][0]
    assert m["direction"] == "outbound" and m["channel"] == "email"
    assert m["body"] == "Your device is ready"


@pytest.mark.asyncio
async def test_client_communication_bad_channel(client, tenant):
    cid = await _client(client, tenant)
    r = await client.post(f"/api/clients/{cid}/communication",
                          json={"channel": "pigeon", "body": "hi"}, headers=tenant)
    assert r.status_code == 422


# ── save custom line to catalog ────────────────────────────
async def _order(client, h, cid):
    return (await client.post("/api/orders", json={
        "location_id": await _loc(client, h), "complaint": "x", "client_id": cid}, headers=h)).json()["id"]


@pytest.mark.asyncio
async def test_custom_line_saved_to_catalog_as_service(client, tenant):
    cid = await _client(client, tenant)
    oid = await _order(client, tenant, cid)
    r = await client.post(f"/api/orders/{oid}/lines", json={
        "line_type": "fee", "kind": "custom", "description": "Bespoke tuning",
        "qty": 1, "unit_price": 500, "save_to_catalog": True, "catalog_kind": "service"}, headers=tenant)
    assert r.status_code in (200, 201), r.text

    found = (await client.get("/api/catalog/search?q=Bespoke&type=service", headers=tenant)).json()["items"]
    assert any(i["name"] == "Bespoke tuning" for i in found)


@pytest.mark.asyncio
async def test_custom_line_saved_to_catalog_as_product(client, tenant):
    cid = await _client(client, tenant)
    oid = await _order(client, tenant, cid)
    r = await client.post(f"/api/orders/{oid}/lines", json={
        "line_type": "fee", "kind": "custom", "description": "Special widget",
        "qty": 2, "unit_price": 30, "cost_price": 10,
        "save_to_catalog": True, "catalog_kind": "product"}, headers=tenant)
    assert r.status_code in (200, 201), r.text
    found = (await client.get("/api/catalog/search?q=Special&type=product", headers=tenant)).json()["items"]
    assert any(i["name"] == "Special widget" for i in found)


@pytest.mark.asyncio
async def test_custom_line_one_off_not_in_catalog(client, tenant):
    cid = await _client(client, tenant)
    oid = await _order(client, tenant, cid)
    await client.post(f"/api/orders/{oid}/lines", json={
        "line_type": "fee", "kind": "custom", "description": "OneOffThing",
        "qty": 1, "unit_price": 50, "save_to_catalog": False}, headers=tenant)
    found = (await client.get("/api/catalog/search?q=OneOffThing", headers=tenant)).json()["items"]
    assert not any(i["name"] == "OneOffThing" for i in found)
