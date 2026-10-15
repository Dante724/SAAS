"""Several items per job (multi-asset): only tools / small engine / phone / computer shops,
off until switched on, item #1 = the job's own device, lines tag to an item, and nothing
crosses shops or jobs."""
import uuid

import pytest


async def _shop(client, industry="tools"):
    r = await client.post("/api/auth/register", json={"org_name": "Shop", "name": "Owner",
                                                      "email": f"o-{uuid.uuid4().hex[:8]}@test.dev", "password": "pw-test-12345"})
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert (await client.put("/api/staff/company", json={"industry_template": industry}, headers=h)).status_code == 200
    return h


async def _job(client, h, with_device=True):
    loc = (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]
    cid = (await client.post("/api/clients", json={"name": "Pat Builder", "phone": "+15550199"}, headers=h)).json()["id"]
    body = {"client_id": cid, "location_id": loc, "complaint": "won't start"}
    dev = None
    if with_device:
        dev = (await client.post("/api/assets", json={"owner_type": "client", "client_id": cid, "brand": "DeWalt", "model": "DCD791", "serial": "D-1"},
                                 headers=h)).json()["id"]
        body["device_id"] = dev
    oid = (await client.post("/api/orders", json=body, headers=h)).json()["id"]
    return oid, cid, dev


async def _on(client, h):
    r = await client.put("/api/staff/company", json={"multi_asset_jobs": True}, headers=h)
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_only_eligible_industries_can_turn_it_on(client):
    auto = await _shop(client, "auto")
    r = await client.put("/api/staff/company", json={"multi_asset_jobs": True}, headers=auto)
    assert r.status_code == 422
    co = (await client.get("/api/staff/company", headers=auto)).json()
    assert co["multi_asset_available"] is False and co["multi_asset_jobs"] is False
    for ind in ("tools", "small_engine", "mobile_phone", "computer"):
        h = await _shop(client, ind)
        await _on(client, h)
        co = (await client.get("/api/staff/company", headers=h)).json()
        assert co["multi_asset_available"] is True and co["multi_asset_jobs"] is True


@pytest.mark.asyncio
async def test_off_by_default_and_writes_refused(client):
    h = await _shop(client)
    oid, _, _ = await _job(client, h)
    r = (await client.get(f"/api/orders/{oid}/items", headers=h)).json()
    assert r == {"available": True, "enabled": False, "items": [], "split_from": None, "split_off": [], "job_unbilled": 0.0}
    assert (await client.post(f"/api/orders/{oid}/items", json={"label": "Saw"}, headers=h)).status_code == 409


@pytest.mark.asyncio
async def test_items_lines_and_outcomes(client):
    h = await _shop(client)
    await _on(client, h)
    oid, cid, dev = await _job(client, h)
    r = (await client.get(f"/api/orders/{oid}/items", headers=h)).json()
    assert r["enabled"] and len(r["items"]) == 1 and r["items"][0]["is_primary"] and r["items"][0]["asset_id"] == dev

    assert (await client.get(f"/api/orders/{oid}/items/candidates", headers=h)).json()["items"] == []
    saw = (await client.post("/api/assets", json={"owner_type": "client", "client_id": cid, "brand": "Makita", "model": "XSH03", "serial": "M-9"},
                             headers=h)).json()["id"]
    assert [d["id"] for d in (await client.get(f"/api/orders/{oid}/items/candidates", headers=h)).json()["items"]] == [saw]
    r = await client.post(f"/api/orders/{oid}/items", json={"asset_id": saw, "complaint": "blade wobbles"}, headers=h)
    assert r.status_code == 201, r.text
    saw_item = r.json()["id"]
    assert (await client.post(f"/api/orders/{oid}/items", json={"asset_id": saw}, headers=h)).status_code == 409
    r = await client.post(f"/api/orders/{oid}/items", json={"label": "Battery pack (no serial)"}, headers=h)
    batt_item = r.json()["id"]
    assert len(r.json()["items"]) == 3

    # tag a line to the saw; totals roll up per item
    r = await client.post(f"/api/orders/{oid}/lines", json={"line_type": "labour", "kind": "custom", "description": "True blade",
                                                            "qty": 1, "unit_price": 40, "order_item_id": saw_item}, headers=h)
    assert r.status_code == 201, r.text
    line = r.json()["line_id"]
    items = {i["id"]: i for i in (await client.get(f"/api/orders/{oid}/items", headers=h)).json()["items"]}
    assert items[saw_item]["line_count"] == 1 and items[saw_item]["total"] == 40
    detail = (await client.get(f"/api/orders/{oid}", headers=h)).json()
    assert [ln["order_item_id"] for ln in detail["lines"]] == [saw_item]
    # move it to "whole job"
    assert (await client.patch(f"/api/orders/{oid}/lines/{line}", json={"order_item_id": ""}, headers=h)).status_code == 200
    detail = (await client.get(f"/api/orders/{oid}", headers=h)).json()
    assert detail["lines"][0]["order_item_id"] is None

    # outcome + collected
    r = await client.patch(f"/api/orders/{oid}/items/{batt_item}", json={"outcome": "declined", "collected": True}, headers=h)
    it = next(i for i in r.json()["items"] if i["id"] == batt_item)
    assert it["outcome"] == "declined" and it["collected_at"]
    assert (await client.patch(f"/api/orders/{oid}/items/{batt_item}", json={"outcome": "maybe"}, headers=h)).status_code == 422

    # item #1 stays; removing another untags its lines
    primary = next(i["id"] for i in items.values() if i["is_primary"])
    assert (await client.delete(f"/api/orders/{oid}/items/{primary}", headers=h)).status_code == 409
    await client.patch(f"/api/orders/{oid}/lines/{line}", json={"order_item_id": saw_item}, headers=h)
    assert (await client.delete(f"/api/orders/{oid}/items/{saw_item}", headers=h)).status_code == 200
    detail = (await client.get(f"/api/orders/{oid}", headers=h)).json()
    assert detail["lines"][0]["order_item_id"] is None


@pytest.mark.asyncio
async def test_switching_industry_hides_items_and_keeps_the_data(client):
    h = await _shop(client)
    await _on(client, h)
    oid, cid, _ = await _job(client, h)
    line = {"line_type": "labour", "kind": "custom", "description": "Grinder service", "qty": 1, "unit_price": 40}
    item = (await client.post(f"/api/orders/{oid}/items", json={"label": "Grinder"}, headers=h)).json()
    r = await client.post(f"/api/orders/{oid}/lines", json={**line, "order_item_id": item["id"]}, headers=h)
    assert r.status_code == 201, r.text
    await client.post("/api/assets", json={"owner_type": "client", "client_id": cid, "brand": "Makita", "serial": "M-1"}, headers=h)
    await client.put("/api/staff/company", json={"industry_template": "auto"}, headers=h)
    # Other industries never see items: not in the job, not as candidates, not on documents.
    r = (await client.get(f"/api/orders/{oid}/items", headers=h)).json()
    assert r["enabled"] is False and r["available"] is False and r["items"] == []
    assert (await client.get(f"/api/orders/{oid}/items/candidates", headers=h)).json() == {"items": []}
    html = (await client.post("/api/documents/print-view", json={"body_html": "{All Items Table}", "doc_type": "work_order",
                                                                  "order_id": oid}, headers=h)).json()["html"]
    assert "Grinder service" in html and "Whole job" not in html and "Grinder subtotal" not in html
    assert (await client.post(f"/api/orders/{oid}/items", json={"label": "Drill"}, headers=h)).status_code == 409
    assert (await client.put("/api/staff/company", json={"multi_asset_jobs": True}, headers=h)).status_code == 422
    # Back to an eligible industry: the items are still there.
    await client.put("/api/staff/company", json={"industry_template": "tools"}, headers=h)
    assert len((await client.get(f"/api/orders/{oid}/items", headers=h)).json()["items"]) == 2


@pytest.mark.asyncio
async def test_every_other_industry_is_refused(client):
    for ind in ("auto", "marine", "powersports", "bike", "fleet_truck", "general"):
        h = await _shop(client, ind)
        assert (await client.put("/api/staff/company", json={"multi_asset_jobs": True}, headers=h)).status_code == 422, ind
        oid, _, _ = await _job(client, h)
        assert (await client.get(f"/api/orders/{oid}/items", headers=h)).json()["available"] is False
        assert (await client.post(f"/api/orders/{oid}/items", json={"label": "x"}, headers=h)).status_code == 409


@pytest.mark.asyncio
async def test_nothing_crosses_shops_or_jobs(client):
    a = await _shop(client)
    b = await _shop(client)
    await _on(client, a)
    await _on(client, b)
    a_job, _, _ = await _job(client, a)
    a_job2, _, _ = await _job(client, a)
    b_job, b_cid, b_dev = await _job(client, b)
    a_item = (await client.post(f"/api/orders/{a_job}/items", json={"label": "Router"}, headers=a)).json()["id"]

    # shop B can't see or touch shop A's job or item
    assert (await client.get(f"/api/orders/{a_job}/items", headers=b)).status_code == 404
    assert (await client.get(f"/api/orders/{a_job}/items/candidates", headers=b)).status_code == 404
    assert (await client.patch(f"/api/orders/{a_job}/items/{a_item}", json={"outcome": "fixed"}, headers=b)).status_code == 404
    # shop A can't attach shop B's asset, or tag a line to another job's item
    assert (await client.post(f"/api/orders/{a_job}/items", json={"asset_id": b_dev}, headers=a)).status_code == 422
    r = await client.post(f"/api/orders/{a_job2}/lines", json={"line_type": "labour", "kind": "custom", "description": "x",
                                                              "qty": 1, "unit_price": 1, "order_item_id": a_item}, headers=a)
    assert r.status_code == 422
    # and B's own item ids don't resolve on A's job
    b_item = (await client.get(f"/api/orders/{b_job}/items", headers=b)).json()["items"][0]["id"]
    assert (await client.patch(f"/api/orders/{a_job}/items/{b_item}", json={"outcome": "fixed"}, headers=a)).status_code == 422


@pytest.mark.asyncio
async def test_asset_must_belong_to_the_jobs_customer(client):
    h = await _shop(client)
    await _on(client, h)
    oid, _, _ = await _job(client, h)
    other = (await client.post("/api/clients", json={"name": "Someone Else"}, headers=h)).json()["id"]
    theirs = (await client.post("/api/assets", json={"owner_type": "client", "client_id": other, "brand": "Bosch"}, headers=h)).json()["id"]
    assert (await client.post(f"/api/orders/{oid}/items", json={"asset_id": theirs}, headers=h)).status_code == 422
