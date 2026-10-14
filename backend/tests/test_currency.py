"""Build 122 — per-document currency: client default, per-order override, rate snapshot."""
import pytest


async def _set_currencies(client, tenant):
    await client.put("/api/staff/company", json={
        "name": "Co", "currency_code": "INR", "currency": "\u20b9",
        "currencies": [{"code": "USD", "symbol": "$", "rate": 0.012},
                       {"code": "EUR", "symbol": "\u20ac", "rate": 0.011}]}, headers=tenant)


async def _loc(client, tenant):
    return (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]


@pytest.mark.asyncio
async def test_order_defaults_to_base_currency(client, tenant):
    await _set_currencies(client, tenant)
    loc = await _loc(client, tenant)
    r = await client.post("/api/orders", json={"location_id": loc, "complaint": "x"}, headers=tenant)
    assert r.status_code == 201, r.text
    oid = r.json()["id"]
    o = (await client.get(f"/api/orders/{oid}", headers=tenant)).json()
    assert o["currency_code"] == "INR"
    assert o["fx_rate"] == 1.0


@pytest.mark.asyncio
async def test_order_inherits_client_currency(client, tenant):
    await _set_currencies(client, tenant)
    loc = await _loc(client, tenant)
    c = (await client.post("/api/clients", json={
        "name": "US Buyer", "phone": "+1", "currency_code": "USD"}, headers=tenant)).json()
    r = await client.post("/api/orders", json={
        "location_id": loc, "client_id": c["id"], "complaint": "x"}, headers=tenant)
    o = (await client.get(f"/api/orders/{r.json()['id']}", headers=tenant)).json()
    assert o["currency_code"] == "USD"
    assert abs(o["fx_rate"] - 0.012) < 1e-9
    assert o["currency_symbol"] == "$"


@pytest.mark.asyncio
async def test_order_override_beats_client(client, tenant):
    await _set_currencies(client, tenant)
    loc = await _loc(client, tenant)
    c = (await client.post("/api/clients", json={
        "name": "US Buyer", "phone": "+12", "currency_code": "USD"}, headers=tenant)).json()
    r = await client.post("/api/orders", json={
        "location_id": loc, "client_id": c["id"], "complaint": "x", "currency_code": "EUR"}, headers=tenant)
    o = (await client.get(f"/api/orders/{r.json()['id']}", headers=tenant)).json()
    assert o["currency_code"] == "EUR"
    assert abs(o["fx_rate"] - 0.011) < 1e-9


@pytest.mark.asyncio
async def test_rate_is_snapshotted(client, tenant):
    await _set_currencies(client, tenant)
    loc = await _loc(client, tenant)
    r = await client.post("/api/orders", json={
        "location_id": loc, "complaint": "x", "currency_code": "USD"}, headers=tenant)
    oid = r.json()["id"]
    # change the org rate afterwards
    await client.put("/api/staff/company", json={
        "name": "Co", "currencies": [{"code": "USD", "symbol": "$", "rate": 0.5}]}, headers=tenant)
    o = (await client.get(f"/api/orders/{oid}", headers=tenant)).json()
    assert abs(o["fx_rate"] - 0.012) < 1e-9   # keeps the original snapshot, not 0.5


@pytest.mark.asyncio
async def test_client_currency_round_trips(client, tenant):
    await _set_currencies(client, tenant)
    c = (await client.post("/api/clients", json={
        "name": "Euro Buyer", "phone": "+13", "currency_code": "EUR"}, headers=tenant)).json()
    got = (await client.get(f"/api/clients/{c['id']}", headers=tenant)).json()
    assert got["currency_code"] == "EUR"
