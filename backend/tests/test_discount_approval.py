"""144hd — discount-approval gate."""


async def _loc(client, tenant):
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()
    return loc[0]["id"] if isinstance(loc, list) else loc["items"][0]["id"]


async def test_approval_threshold_round_trips(client, tenant):
    r = await client.put("/api/pricing/defaults", json={"discount_approval_pct": 25}, headers=tenant)
    assert r.status_code == 200, r.text
    got = (await client.get("/api/pricing/defaults", headers=tenant)).json()
    assert got["discount_approval_pct"] == 25


async def test_manager_can_exceed_threshold(client, tenant):
    # the registered account is an owner/manager -> a big discount is allowed
    await client.put("/api/pricing/defaults", json={"discount_approval_pct": 20}, headers=tenant)
    loc_id = await _loc(client, tenant)
    o = (await client.post("/api/orders", json={"location_id": loc_id, "complaint": "x"}, headers=tenant)).json()
    r = await client.post(f"/api/orders/{o['id']}/lines", json={
        "line_type": "labour", "kind": "service", "description": "Big off",
        "unit_price": 100, "discount": 40, "discount_type": "pct"}, headers=tenant)
    assert r.status_code == 201, r.text
