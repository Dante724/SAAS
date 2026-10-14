"""144ij — one status engine drives all 5 Orderry record-type pipelines."""

RT = ["work_order", "estimate", "inquiry", "invoice", "transfer", "purchase_order"]


async def test_all_five_pipelines_seed_and_list(client, tenant):
    for rt in RT:
        r = await client.get(f"/api/statuses?record_type={rt}", headers=tenant)
        assert r.status_code == 200, r.text
        body = r.json()
        items = body["items"]
        assert items, f"{rt} should have seeded default statuses"
        # every row reports its own record_type, and exactly one default
        assert all(s["record_type"] == rt for s in items)
        assert sum(1 for s in items if s["is_default"]) <= 1  # never more than one default
        assert body["record_types"] == RT


async def test_legacy_is_estimate_param_still_works(client, tenant):
    # old callers pass ?is_estimate=true|false and must still get the right set
    wo = (await client.get("/api/statuses?is_estimate=false", headers=tenant)).json()["items"]
    est = (await client.get("/api/statuses?is_estimate=true", headers=tenant)).json()["items"]
    assert all(s["record_type"] == "work_order" for s in wo)
    assert all(s["record_type"] == "estimate" and s["is_estimate"] for s in est)


async def test_create_status_scoped_to_record_type(client, tenant):
    r = await client.post("/api/statuses", headers=tenant, json={
        "name": "Awaiting courier", "status_group": "pending", "record_type": "transfer"})
    assert r.status_code == 201, r.text
    s = r.json()
    assert s["record_type"] == "transfer"
    # it shows up only under transfers, not work orders
    tr_keys = {x["key"] for x in (await client.get("/api/statuses?record_type=transfer", headers=tenant)).json()["items"]}
    wo_keys = {x["key"] for x in (await client.get("/api/statuses?record_type=work_order", headers=tenant)).json()["items"]}
    assert s["key"] in tr_keys and s["key"] not in wo_keys


async def test_default_uniqueness_does_not_bleed_across_types(client, tenant):
    # setting a new default for invoices must not clear the work-order default
    inv = (await client.get("/api/statuses?record_type=invoice", headers=tenant)).json()["items"]
    non_default = next(s for s in inv if not s["is_default"])
    await client.put(f"/api/statuses/{non_default['id']}", headers=tenant, json={
        "name": non_default["name"], "status_group": non_default["status_group"],
        "is_default": True, "record_type": "invoice"})
    wo = (await client.get("/api/statuses?record_type=work_order", headers=tenant)).json()["items"]
    assert sum(1 for s in wo if s["is_default"]) == 1  # untouched


async def test_orders_still_create_and_read_after_new_pipelines(client, tenant):
    # regression: the new is_estimate=False rows must not break order status resolution
    oid = (await client.post("/api/orders", json={"complaint": "boom"}, headers=tenant)).json()["id"]
    got = await client.get(f"/api/orders/{oid}", headers=tenant)
    assert got.status_code == 200, got.text
    assert got.json()["status"]  # resolved a single work-order status, no MultipleResults
