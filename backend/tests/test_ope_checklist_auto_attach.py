"""Team E4 — checklist templates that attach themselves when an order enters a status.

- auto_status_keys is saved, validated (real status of that record type) and round-trips.
- Entering the status attaches the template once per order, however often the status is entered.
- Record type and the template's job-type scope are respected; inactive templates never attach.
- It combines with require_checklist: the checklist attached on entering a status doesn't block
  entering it, but the next require_checklist status (and the close gate) waits for it.
"""
import uuid

import pytest


async def _register(client):
    email = f"e4-{uuid.uuid4().hex[:8]}@t.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Co", "name": "Admin", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _tpl(name, keys, **extra):
    return {"name": name, "scope": "all", "auto_status_keys": keys, **extra, "steps": [
        {"title": "QC", "sections": [{"title": "", "items": [
            {"label": "Powers on", "field_kind": "check_result"},
            {"label": "Notes", "field_kind": "textarea", "required": False}]}]}]}


async def _loc(client, H):
    return (await client.get("/api/staff/locations", headers=H)).json()["items"][0]["id"]


async def _order(client, H, **extra):
    r = await client.post("/api/orders", json={"location_id": await _loc(client, H), "complaint": "x", **extra}, headers=H)
    assert r.status_code in (200, 201), r.text
    return r.json()


async def _move(client, H, oid, key):
    return await client.patch(f"/api/orders/{oid}/status", json={"status": key}, headers=H)


async def _checklists(client, H, oid):
    return (await client.get(f"/api/orders/{oid}/checklists", headers=H)).json()["items"]


WO = lambda key: {"record_type": "work_order", "key": key}  # noqa: E731


@pytest.mark.asyncio
async def test_auto_status_keys_round_trip_and_validation(client):
    H = await _register(client)
    r = await client.post("/api/checklist-templates", json=_tpl("Repair QC", [WO("in_repair"), WO("in_repair")]), headers=H)
    assert r.status_code == 201, r.text
    t = r.json()
    assert t["auto_status_keys"] == [WO("in_repair")]                     # de-duplicated
    listed = next(x for x in (await client.get("/api/checklist-templates", headers=H)).json()["items"] if x["id"] == t["id"])
    assert listed["auto_status_keys"] == [WO("in_repair")]

    # an older client that doesn't send the field keeps it
    body = _tpl("Repair QC", None)
    body.pop("auto_status_keys")
    assert (await client.put(f"/api/checklist-templates/{t['id']}", json=body, headers=H)).json()["auto_status_keys"] == [WO("in_repair")]
    # [] clears it
    assert (await client.put(f"/api/checklist-templates/{t['id']}", json=_tpl("Repair QC", []), headers=H)).json()["auto_status_keys"] == []

    bad = await client.post("/api/checklist-templates", json=_tpl("Bad", [WO("no_such_status")]), headers=H)
    assert bad.status_code == 422
    wrong_type = await client.post("/api/checklist-templates", json=_tpl("Bad2", [{"record_type": "estimate", "key": "in_repair"}]), headers=H)
    assert wrong_type.status_code == 422
    junk = await client.post("/api/checklist-templates", json=_tpl("Bad3", [{"record_type": "invoice", "key": "inv_new"}]), headers=H)
    assert junk.status_code == 422


@pytest.mark.asyncio
async def test_attaches_once_per_order_on_entering_the_status(client):
    H = await _register(client)
    t = (await client.post("/api/checklist-templates", json=_tpl("Repair QC", [WO("in_repair")]), headers=H)).json()
    o = await _order(client, H)
    assert await _checklists(client, H, o["id"]) == []
    assert (await _move(client, H, o["id"], "diagnosed")).status_code == 200
    assert await _checklists(client, H, o["id"]) == []                  # not this status

    for key in ("in_repair", "waiting_parts", "in_repair", "in_repair"):  # re-entered, and a no-op move
        assert (await _move(client, H, o["id"], key)).status_code == 200
    cls = await _checklists(client, H, o["id"])
    assert [c["title"] for c in cls] == ["Repair QC"] and cls[0]["items_total"] == 2

    # a template already attached by hand is not attached again
    o2 = await _order(client, H)
    assert (await client.post(f"/api/orders/{o2['id']}/checklists", json={"template_id": t["id"]}, headers=H)).status_code == 201
    await _move(client, H, o2["id"], "in_repair")
    assert len(await _checklists(client, H, o2["id"])) == 1

    # the order's timeline says it happened
    feed = await client.get(f"/api/feed/order/{o['id']}", headers=H)
    assert feed.status_code == 200 and feed.text.count("Checklist attached: Repair QC") == 1


@pytest.mark.asyncio
async def test_respects_record_type_job_type_and_active(client):
    H = await _register(client)
    wt = (await client.post("/api/forms/types", json={"name": "Warranty"}, headers=H)).json()["id"]
    await client.post("/api/checklist-templates", json=_tpl("Estimate QC", [{"record_type": "estimate", "key": "est_progress"}]), headers=H)
    await client.post("/api/checklist-templates", json=_tpl("Warranty QC", [WO("in_repair")], scope="selected", type_ids=[wt]), headers=H)
    gone = (await client.post("/api/checklist-templates", json=_tpl("Old QC", [WO("in_repair")]), headers=H)).json()
    assert (await client.delete(f"/api/checklist-templates/{gone['id']}", headers=H)).status_code == 200

    plain = await _order(client, H)
    await _move(client, H, plain["id"], "in_repair")
    assert await _checklists(client, H, plain["id"]) == []              # wrong job type, inactive template

    warranty = await _order(client, H, type_id=wt)
    await _move(client, H, warranty["id"], "in_repair")
    assert [c["title"] for c in await _checklists(client, H, warranty["id"])] == ["Warranty QC"]

    est = await _order(client, H, is_estimate=True)
    r = await _move(client, H, est["id"], "est_progress")
    assert r.status_code == 200, r.text
    assert [c["title"] for c in await _checklists(client, H, est["id"])] == ["Estimate QC"]


@pytest.mark.asyncio
async def test_new_order_gets_the_template_for_its_first_status(client):
    H = await _register(client)
    await client.post("/api/checklist-templates", json=_tpl("Intake", [WO("new")]), headers=H)
    o = await _order(client, H)
    assert [c["title"] for c in await _checklists(client, H, o["id"])] == ["Intake"]


@pytest.mark.asyncio
async def test_combines_with_require_checklist_gate(client):
    H = await _register(client)
    r = await client.post("/api/statuses", json={
        "name": "QC passed", "key": "qc_passed", "status_group": "in_progress", "require_checklist": True}, headers=H)
    assert r.status_code in (200, 201), r.text
    await client.post("/api/checklist-templates", json=_tpl("Repair QC", [WO("in_repair")]), headers=H)
    # a gated status that itself attaches a template can still be entered
    await client.post("/api/checklist-templates", json=_tpl("QC sign-off", [WO("qc_passed")]), headers=H)

    o = await _order(client, H)
    assert (await _move(client, H, o["id"], "in_repair")).status_code == 200
    blocked = await _move(client, H, o["id"], "qc_passed")
    assert blocked.status_code == 409 and "checklist" in blocked.text.lower()

    cl = (await _checklists(client, H, o["id"]))[0]
    items = (await client.get(f"/api/orders/{o['id']}/checklists/{cl['id']}", headers=H)).json()["items"]
    for it in items:
        val = "pass" if it["field_kind"] == "check_result" else "ok"
        assert (await client.patch(f"/api/orders/{o['id']}/checklists/{cl['id']}/items/{it['id']}",
                                   json={"value": val}, headers=H)).status_code == 200
    assert (await _move(client, H, o["id"], "qc_passed")).status_code == 200
    titles = [c["title"] for c in await _checklists(client, H, o["id"])]
    assert titles == ["Repair QC", "QC sign-off"]                        # attached after the gate passed

    # the newly attached required check now holds the close until it's done
    closing = await _move(client, H, o["id"], "done")
    assert closing.status_code == 409 and "checklist" in closing.text.lower()


@pytest.mark.asyncio
async def test_status_change_by_a_role_without_checklist_rights_still_attaches(client):
    """Auto-attach is the shop's rule, not the mover's action: a technician who can't add
    checklists by hand still gets the one the status brings."""
    H = await _register(client)
    role = (await client.post("/api/settings/rbac/roles", json={
        "name": "Mover", "template": "Specialist", "permissions": {"can_create_checklist": False}}, headers=H)).json()
    email = f"m-{uuid.uuid4().hex[:8]}@t.dev"
    await client.post("/api/staff/employees", json={"name": "Mo", "email": email, "password": "pw-test-12345",
                                                    "role_id": role["id"]}, headers=H)
    T = {"Authorization": "Bearer " + (await client.post("/api/auth/token", data={
        "username": email, "password": "pw-test-12345"})).json()["access_token"]}
    t = (await client.post("/api/checklist-templates", json=_tpl("Repair QC", [WO("in_repair")]), headers=H)).json()
    o = await _order(client, H)
    assert (await client.post(f"/api/orders/{o['id']}/checklists", json={"template_id": t["id"]}, headers=T)).status_code == 403
    assert (await _move(client, T, o["id"], "in_repair")).status_code == 200
    assert len(await _checklists(client, H, o["id"])) == 1
