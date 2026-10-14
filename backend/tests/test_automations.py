"""Build 144w — workflow automation engine + rules API."""
import pytest


async def _order(client, tenant, **extra):
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    cid = (await client.post("/api/clients", json={"name": "Auto Co"}, headers=tenant)).json()["id"]
    body = {"client_id": cid, "location_id": loc, "complaint": "x"}
    body.update(extra)
    return (await client.post("/api/orders", json=body, headers=tenant)).json()["id"]


async def _a_status(client, tenant):
    """A non-default work-order status key we can transition into."""
    st = (await client.get("/api/statuses?is_estimate=false", headers=tenant)).json()["items"]
    # pick a status that isn't the current/default one
    return st[-1]["key"], st


@pytest.mark.asyncio
async def test_meta_and_crud(client, tenant):
    meta = (await client.get("/api/automations/meta", headers=tenant)).json()
    assert any(e["key"] == "status_changed" for e in meta["events"])
    assert any(a["key"] == "create_task" for a in meta["actions"])

    r = await client.post("/api/automations", json={
        "name": "Ready → task", "event": "status_changed", "trigger_status": "ready",
        "action_type": "create_task", "action_config": {"title": "Call the customer"}},
        headers=tenant)
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    items = (await client.get("/api/automations", headers=tenant)).json()["items"]
    assert any(x["id"] == rid and x["action_type"] == "create_task" for x in items)

    # update + delete
    upd = await client.put(f"/api/automations/{rid}", json={
        "name": "Ready → task (v2)", "event": "status_changed", "trigger_status": "ready",
        "action_type": "create_task", "action_config": {"title": "Call now"}, "active": False},
        headers=tenant)
    assert upd.status_code == 200
    assert (await client.delete(f"/api/automations/{rid}", headers=tenant)).status_code == 200

    # invalid event/action rejected
    bad = await client.post("/api/automations", json={
        "name": "x", "event": "nope", "action_type": "create_task"}, headers=tenant)
    assert bad.status_code == 422


@pytest.mark.asyncio
async def test_status_change_creates_task(client, tenant):
    key, _ = await _a_status(client, tenant)
    await client.post("/api/automations", json={
        "name": "On status → task", "event": "status_changed", "trigger_status": key,
        "action_type": "create_task", "action_config": {"title": "Prep pickup"}}, headers=tenant)
    oid = await _order(client, tenant)
    # no task yet
    assert (await client.get(f"/api/tasks?order_id={oid}", headers=tenant)).json()["items"] == []
    # transition into the trigger status
    r = await client.patch(f"/api/orders/{oid}/status", json={"status": key}, headers=tenant)
    assert r.status_code == 200, r.text
    tasks = (await client.get(f"/api/tasks?order_id={oid}", headers=tenant)).json()["items"]
    assert any(t["title"] == "Prep pickup" for t in tasks)


@pytest.mark.asyncio
async def test_conditions_gate_firing(client, tenant):
    key, _ = await _a_status(client, tenant)
    # only fire when order total >= 100
    await client.post("/api/automations", json={
        "name": "Big orders only", "event": "status_changed", "trigger_status": key,
        "conditions": [{"field": "total", "op": "gte", "value": 100}],
        "action_type": "add_tag", "action_config": {"tag": "high-value"}}, headers=tenant)

    # cheap order — condition fails, no tag
    cheap = await _order(client, tenant, lines=[{"line_type": "part", "description": "cable", "qty": 1, "unit_price": 5}])
    await client.patch(f"/api/orders/{cheap}/status", json={"status": key}, headers=tenant)
    o1 = (await client.get(f"/api/orders/{cheap}", headers=tenant)).json()
    assert "high-value" not in ((o1.get("custom_data") or {}).get("tags") or [])

    # pricey order — condition passes, tag applied
    rich = await _order(client, tenant, lines=[{"line_type": "part", "description": "board", "qty": 1, "unit_price": 250}])
    await client.patch(f"/api/orders/{rich}/status", json={"status": key}, headers=tenant)
    o2 = (await client.get(f"/api/orders/{rich}", headers=tenant)).json()
    assert "high-value" in ((o2.get("custom_data") or {}).get("tags") or [])


@pytest.mark.asyncio
async def test_test_endpoint_dry_run(client, tenant):
    key, _ = await _a_status(client, tenant)
    rid = (await client.post("/api/automations", json={
        "name": "match ready", "event": "status_changed", "trigger_status": key,
        "action_type": "create_task", "action_config": {}}, headers=tenant)).json()["id"]
    oid = await _order(client, tenant)
    await client.patch(f"/api/orders/{oid}/status", json={"status": key}, headers=tenant)
    res = (await client.get(f"/api/automations/{rid}/test", headers=tenant)).json()
    assert res["would_match"] >= 1


@pytest.mark.asyncio
async def test_sweep_fires_overdue(client, tenant):
    # a rule on the overdue event
    await client.post("/api/automations", json={
        "name": "Overdue → task", "event": "order_overdue",
        "action_type": "create_task", "action_config": {"title": "Chase overdue job"}}, headers=tenant)
    # an order whose promised time is in the past and still open
    oid = await _order(client, tenant)
    await client.patch(f"/api/orders/{oid}/schedule",
                       json={"scheduled_to": "2020-01-01T09:00:00"}, headers=tenant)
    res = (await client.post("/api/automations/sweep", headers=tenant)).json()
    assert res["fired"] >= 1
    tasks = (await client.get(f"/api/tasks?order_id={oid}", headers=tenant)).json()["items"]
    assert any(t["title"] == "Chase overdue job" for t in tasks)
