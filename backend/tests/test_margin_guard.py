"""144gy — margin-floor guardrail: defaults round-trip + margin-check verdict."""


async def test_pricing_defaults_carry_margin_guard(client, tenant):
    r = await client.put("/api/pricing/defaults",
                         json={"margin_guard": "block", "min_margin_pct": 40}, headers=tenant)
    assert r.status_code == 200, r.text
    got = (await client.get("/api/pricing/defaults", headers=tenant)).json()
    assert got["margin_guard"] == "block"
    assert got["min_margin_pct"] == 40


async def test_margin_check_flags_below_floor(client, tenant):
    await client.put("/api/pricing/defaults",
                     json={"margin_guard": "block", "min_margin_pct": 50}, headers=tenant)
    # cost 60, sell 100 -> 40% margin, floor 50% -> blocked
    low = (await client.post("/api/pricing/margin-check",
           json={"unit_price": 100, "qty": 1, "cost_price": 60}, headers=tenant)).json()
    assert low["margin_pct"] == 40.0
    assert low["below_floor"] is True and low["blocked"] is True
    # cost 30, sell 100 -> 70% margin -> ok
    hi = (await client.post("/api/pricing/margin-check",
          json={"unit_price": 100, "qty": 1, "cost_price": 30}, headers=tenant)).json()
    assert hi["margin_pct"] == 70.0
    assert hi["below_floor"] is False and hi["blocked"] is False


async def _loc(client, tenant):
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()
    return loc[0]["id"] if isinstance(loc, list) else loc["items"][0]["id"]


async def test_guard_blocks_on_add_and_edit(client, tenant):
    await client.put("/api/pricing/defaults",
                     json={"margin_guard": "block", "min_margin_pct": 50}, headers=tenant)
    loc_id = await _loc(client, tenant)
    o = (await client.post("/api/orders", json={"location_id": loc_id, "complaint": "x"}, headers=tenant)).json()
    # add at full price (70% margin) — ok
    r = await client.post(f"/api/orders/{o['id']}/lines", json={
        "line_type": "part", "kind": "product", "description": "Widget",
        "unit_price": 100, "cost_price": 30}, headers=tenant)
    assert r.status_code == 201, r.text
    line_id = r.json()["line_id"]
    # add below floor directly — blocked
    bad = await client.post(f"/api/orders/{o['id']}/lines", json={
        "line_type": "part", "kind": "product", "description": "Cheap",
        "unit_price": 100, "cost_price": 60}, headers=tenant)
    assert bad.status_code == 409, bad.text
    # edit the good line down to a 40% margin via a discount — the loophole, now blocked
    edit = await client.patch(f"/api/orders/{o['id']}/lines/{line_id}",
                              json={"discount": 60}, headers=tenant)  # 100*(1-.6)=40 vs cost 30 -> 25% margin
    assert edit.status_code == 409, edit.text


async def test_guard_blocks_on_order_create(client, tenant):
    await client.put("/api/pricing/defaults",
                     json={"margin_guard": "block", "min_margin_pct": 50}, headers=tenant)
    loc_id = await _loc(client, tenant)
    r = await client.post("/api/orders", json={"location_id": loc_id, "complaint": "x", "lines": [
        {"line_type": "part", "kind": "product", "description": "Loss", "unit_price": 100, "cost_price": 80}]},
        headers=tenant)
    assert r.status_code == 409, r.text
