"""144he — quantity break tiers: a tier markup kicks in at min_qty."""


async def _loc(client, tenant):
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()
    return loc[0]["id"] if isinstance(loc, list) else loc["items"][0]["id"]


async def test_tier_crud_round_trip(client, tenant):
    pt = (await client.post("/api/pricing/types", json={"name": "Wholesale", "markup_pct": 100}, headers=tenant)).json()
    r = await client.post(f"/api/pricing/types/{pt['id']}/tiers", json={"min_qty": 10, "markup_pct": 20}, headers=tenant)
    assert r.status_code == 201, r.text
    lst = (await client.get(f"/api/pricing/types/{pt['id']}/tiers", headers=tenant)).json()
    assert len(lst["items"]) == 1 and lst["items"][0]["min_qty"] == 10


async def test_tier_markup_applies_at_qty(client, tenant):
    # product cost 50; base markup 100% -> 100; tier at qty>=10 -> 20% -> 60
    pt = (await client.post("/api/pricing/types", json={"name": "Bulk", "markup_pct": 100}, headers=tenant)).json()
    await client.post(f"/api/pricing/types/{pt['id']}/tiers", json={"min_qty": 10, "markup_pct": 20}, headers=tenant)
    prod = (await client.post("/api/catalog/products",
            json={"name": "Bolt", "cost_price": 50, "sell_price": 999}, headers=tenant)).json()
    cl = (await client.post("/api/clients", json={"name": "Bulk buyer",
          "disc_materials_mode": "price_type", "disc_materials_price_type_id": pt["id"]}, headers=tenant)).json()
    loc_id = await _loc(client, tenant)

    async def _line_price(qty):
        o = (await client.post("/api/orders", json={"location_id": loc_id, "client_id": cl["id"], "complaint": "x"}, headers=tenant)).json()
        r = await client.post(f"/api/orders/{o['id']}/lines", json={
            "line_type": "part", "kind": "product", "ref_id": prod["id"], "description": "Bolt",
            "unit_price": 999, "qty": qty}, headers=tenant)
        assert r.status_code == 201, r.text
        det = (await client.get(f"/api/orders/{o['id']}", headers=tenant)).json()
        return round(det["lines"][0]["unit_price"])

    assert await _line_price(1) == 100    # base markup 100%
    assert await _line_price(10) == 60     # tier at qty>=10 -> 20%
