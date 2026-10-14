"""144jw — asset card (AssetDrawer) data: /assets/{id} carries linked-order history;
/assets/{id}/movements returns the lifecycle log. These feed the Work-orders and
Repair-history tabs that were previously stubs."""


async def test_asset_detail_has_history_and_movements(client, tenant):
    a = (await client.post("/api/assets", headers=tenant, json={
        "owner_type": "company", "asset_type": "Device", "brand": "Acme", "model": "X1", "serial": "SN-144JW"})).json()
    aid = a["id"]

    detail = (await client.get(f"/api/assets/{aid}", headers=tenant)).json()
    assert "history" in detail and isinstance(detail["history"], list)   # Work orders tab source

    mv = await client.get(f"/api/assets/{aid}/movements", headers=tenant)
    assert mv.status_code == 200 and isinstance(mv.json().get("items"), list)  # Repair history tab source
