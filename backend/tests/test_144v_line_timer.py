"""144v — multiple techs time their own labour line on one work order.

Proves a timer attributes to a specific order_line_id, the /labor endpoint
reports who's on the job (by_employee) with the line label, and a line that
doesn't belong to the order is rejected.
"""


async def _order_with_labour(client, tenant):
    cid = (await client.post("/api/clients", json={"name": "Multi Co", "phone": "+15550222"}, headers=tenant)).json()["id"]
    r = await client.post("/api/orders", headers=tenant, json={
        "client_id": cid, "complaint": "multi-tech",
        "lines": [{"line_type": "labour", "description": "Diagnostics", "qty": 1, "unit_price": 60},
                  {"line_type": "labour", "description": "Alignment", "qty": 1, "unit_price": 90}]})
    assert r.status_code in (200, 201), r.text
    o = r.json()
    labour_lines = [ln for ln in o["lines"] if ln["line_type"] == "labour"]
    return o["id"], labour_lines[0]["id"], labour_lines[1]["id"]


async def test_timer_attributes_to_a_line_and_labor_reports_it(client, tenant):
    oid, line_a, _line_b = await _order_with_labour(client, tenant)

    r = await client.post("/api/timeclock/start?switch=true",
                          json={"order_id": oid, "order_line_id": line_a, "category": "job"}, headers=tenant)
    assert r.status_code in (200, 201), r.text
    assert r.json()["order_line_id"] == line_a

    labor = (await client.get(f"/api/orders/{oid}/labor", headers=tenant)).json()
    assert labor["running"] is True
    assert any(b["running"] for b in labor["by_employee"])          # WO panel: someone's on it
    ent = next(e for e in labor["entries"] if e["order_line_id"] == line_a)
    assert ent["line_description"] == "Diagnostics" and ent["employee_name"]

    await client.post("/api/timeclock/stop", headers=tenant)


async def test_stop_specific_entry_by_id(client, tenant):
    oid, line_a, _ = await _order_with_labour(client, tenant)
    started = (await client.post("/api/timeclock/start?switch=true",
                                 json={"order_id": oid, "order_line_id": line_a, "category": "job"}, headers=tenant)).json()
    eid = started["id"]
    # admin (a manager) stops that entry by id
    r = await client.post(f"/api/timeclock/entries/{eid}/stop", headers=tenant)
    assert r.status_code == 200, r.text
    assert r.json()["running"] is False
    # stopping it again → 400 (no longer running)
    assert (await client.post(f"/api/timeclock/entries/{eid}/stop", headers=tenant)).status_code == 400


async def test_line_from_another_order_is_rejected(client, tenant):
    oid_a, line_a, _ = await _order_with_labour(client, tenant)
    oid_b, _, _ = await _order_with_labour(client, tenant)
    # line_a belongs to order A, so timing it against order B must 400
    r = await client.post("/api/timeclock/start?switch=true",
                          json={"order_id": oid_b, "order_line_id": line_a, "category": "job"}, headers=tenant)
    assert r.status_code == 400, r.text
