"""Client PATCH — partial update powering the drawer's discount matrix."""


async def test_patch_client_discount_matrix(client, tenant):
    c = (await client.post("/api/clients", json={"name": "Acme Co"}, headers=tenant)).json()
    cid = c["id"]
    # partial patch: only the discount fields
    r = await client.patch(f"/api/clients/{cid}", json={
        "disc_services_mode": "percent", "disc_services_value": 12,
        "disc_materials_mode": "price_type"}, headers=tenant)
    assert r.status_code == 200, r.text
    got = (await client.get(f"/api/clients/{cid}", headers=tenant)).json()
    assert got["name"] == "Acme Co"                      # untouched
    assert got["disc_services_mode"] == "percent"
    assert got["disc_services_value"] == 12
    assert got["disc_materials_mode"] == "price_type"
