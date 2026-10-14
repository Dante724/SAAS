"""144ka — the WO/Estimate create flow's "+ More details" asset fields:
POST /orders with asset_imei/vin/hin/color/condition auto-creates the device
carrying every identifier, and they round-trip on the asset detail card."""


async def _client(client, tenant, name="Asset Details Co"):
    return (await client.post("/api/clients", json={"name": name, "phone": "+15550777"}, headers=tenant)).json()["id"]


async def test_order_create_persists_extra_asset_details(client, tenant):
    cid = await _client(client, tenant)
    r = await client.post("/api/orders", headers=tenant, json={
        "client_id": cid, "complaint": "won't boot",
        "asset_brand": "Samsung", "asset_model": "Galaxy S24", "asset_serial": "SN-KA-1",
        "asset_imei": "350099001761481", "asset_vin": "VIN-KA-1", "asset_hin": "HIN-KA-1",
        "asset_color": "Titanium Black", "asset_condition": "Good",
    })
    assert r.status_code in (200, 201), r.text
    dev = r.json()["device"]
    assert dev and dev["id"], "order should auto-create the asset"
    assert dev["imei"] == "350099001761481" and dev["vin"] == "VIN-KA-1" and dev["hin"] == "HIN-KA-1"

    # color + condition aren't on the order's device block — verify on the asset card
    d = (await client.get(f"/api/assets/{dev['id']}", headers=tenant)).json()
    assert d["color"] == "Titanium Black"
    assert d["condition"] == "Good"
    assert d["brand"] == "Samsung" and d["serial"] == "SN-KA-1"


async def test_color_or_condition_alone_still_creates_asset(client, tenant):
    # even with no brand/serial, a colour/condition on a client order spins up the asset
    cid = await _client(client, tenant, name="Colour Only Co")
    r = await client.post("/api/orders", headers=tenant, json={
        "client_id": cid, "asset_color": "Red", "asset_condition": "Fair"})
    assert r.status_code in (200, 201), r.text
    dev = r.json()["device"]
    assert dev and dev["id"]
    d = (await client.get(f"/api/assets/{dev['id']}", headers=tenant)).json()
    assert d["color"] == "Red" and d["condition"] == "Fair"
