"""144jx — creating an asset from the shared AssetDrawer (Assets hub + Inventory Equipment):
POST /assets persists identifiers + custom_data, and it comes back on the detail card."""


async def test_create_company_asset_with_custom_fields(client, tenant):
    r = await client.post("/api/assets", headers=tenant, json={
        "owner_type": "company", "asset_type": "Vehicle", "brand": "Ford", "model": "Transit",
        "serial": "SN-CREATE-1", "custom_data": {"number_plate": "AB12 CDE", "year": "2021"}})
    assert r.status_code == 201, r.text
    aid = r.json()["id"]

    d = (await client.get(f"/api/assets/{aid}", headers=tenant)).json()
    assert d["brand"] == "Ford" and d["model"] == "Transit"
    assert d["custom_data"]["number_plate"] == "AB12 CDE"   # added fields round-trip
    assert d["custom_data"]["year"] == "2021"


async def test_create_client_asset_requires_owner(client, tenant):
    # client-owned with no client_id → rejected (the drawer guards this client-side too)
    r = await client.post("/api/assets", headers=tenant, json={"owner_type": "client", "brand": "X"})
    assert r.status_code == 400
