"""Build 144dp — checklists v2: nested templates, typed fields, per-order fill,
derived result, tenant isolation, and permissions."""
import uuid

import pytest


async def _register(client):
    email = f"a-{uuid.uuid4().hex[:8]}@t.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Co", "name": "Admin", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    b = r.json()
    return {"Authorization": f"Bearer {b['access_token']}"}, b["org_id"]


def _tpl_payload(name="Repair QC"):
    return {
        "name": name, "scope": "all",
        "steps": [
            {"title": "Diagnostics", "sections": [
                {"title": "Technical", "allow_notes": True, "items": [
                    {"label": "Battery health", "field_kind": "check_result", "affects_result": True},
                    {"label": "Screen assembly", "field_kind": "check_result", "affects_result": True,
                     "client_visible": True},
                    {"label": "Notes", "field_kind": "textarea", "required": False, "affects_result": False},
                ]},
            ]},
        ],
    }


async def _order(client, H):
    loc = (await client.get("/api/staff/locations", headers=H)).json()
    loc_id = (loc[0]["id"] if isinstance(loc, list) else loc["items"][0]["id"])
    return (await client.post("/api/orders", json={"location_id": loc_id, "complaint": "x"}, headers=H)).json()


@pytest.mark.asyncio
async def test_template_crud_nested(client):
    H, _ = await _register(client)
    r = await client.post("/api/checklist-templates", json=_tpl_payload(), headers=H)
    assert r.status_code == 201, r.text
    t = r.json()
    assert t["steps"][0]["title"] == "Diagnostics"
    assert t["steps"][0]["sections"][0]["items"][0]["field_kind"] == "check_result"
    assert len(t["steps"][0]["sections"][0]["items"]) == 3

    # duplicate name rejected
    dup = await client.post("/api/checklist-templates", json=_tpl_payload(), headers=H)
    assert dup.status_code == 409

    # update replaces structure
    p = _tpl_payload()
    p["steps"][0]["title"] = "Intake"
    up = await client.put(f"/api/checklist-templates/{t['id']}", json=p, headers=H)
    assert up.status_code == 200
    assert up.json()["steps"][0]["title"] == "Intake"


@pytest.mark.asyncio
async def test_attach_fill_and_derive_result(client):
    H, _ = await _register(client)
    t = (await client.post("/api/checklist-templates", json=_tpl_payload(), headers=H)).json()
    o = await _order(client, H)

    # attach snapshots the fields
    at = await client.post(f"/api/orders/{o['id']}/checklists", json={"template_id": t["id"]}, headers=H)
    assert at.status_code == 201, at.text
    cid = at.json()["id"]

    full = (await client.get(f"/api/orders/{o['id']}/checklists/{cid}", headers=H)).json()
    items = full["items"]
    assert len(items) == 3 and items[0]["step_title"] == "Diagnostics"

    # a bad check-result value is rejected
    bad = await client.patch(f"/api/orders/{o['id']}/checklists/{cid}/items/{items[0]['id']}",
                             json={"value": "banana"}, headers=H)
    assert bad.status_code == 422

    # pass the first, fail the second (affects result), fill the note
    await client.patch(f"/api/orders/{o['id']}/checklists/{cid}/items/{items[0]['id']}", json={"value": "pass"}, headers=H)
    await client.patch(f"/api/orders/{o['id']}/checklists/{cid}/items/{items[1]['id']}", json={"value": "fail"}, headers=H)
    last = await client.patch(f"/api/orders/{o['id']}/checklists/{cid}/items/{items[2]['id']}",
                              json={"value": "cracked glass"}, headers=H)
    s = last.json()["summary"]
    assert s["status"] == "completed"          # note is optional, both required checks answered
    assert s["result"] == "failed"             # a Fail on an affects_result item fails the checklist

    # progress rollup + list
    prog = (await client.get(f"/api/orders/{o['id']}/checklist-progress", headers=H)).json()
    assert prog["checklists"] == 1 and prog["unfinished"] == 0
    lst = (await client.get(f"/api/orders/{o['id']}/checklists", headers=H)).json()
    assert lst["items"][0]["result"] == "failed" and lst["items"][0]["author"] == "Admin"


@pytest.mark.asyncio
async def test_tenant_isolation(client):
    A, _ = await _register(client)
    B, _ = await _register(client)
    t = (await client.post("/api/checklist-templates", json=_tpl_payload(), headers=A)).json()
    o = await _order(client, A)
    cid = (await client.post(f"/api/orders/{o['id']}/checklists", json={"template_id": t["id"]}, headers=A)).json()["id"]

    # B cannot see A's template or checklist
    assert (await client.get(f"/api/checklist-templates/{t['id']}", headers=B)).status_code == 404
    assert (await client.get(f"/api/orders/{o['id']}/checklists/{cid}", headers=B)).status_code == 404
    assert (await client.post(f"/api/orders/{o['id']}/checklists", json={"template_id": t["id"]}, headers=B)).status_code == 404


@pytest.mark.asyncio
async def test_fail_becomes_recommendation_and_client_visible_on_track(client):
    H, _ = await _register(client)
    t = (await client.post("/api/checklist-templates", json=_tpl_payload(), headers=H)).json()
    o = await _order(client, H)
    cid = (await client.post(f"/api/orders/{o['id']}/checklists", json={"template_id": t["id"]}, headers=H)).json()["id"]
    full = (await client.get(f"/api/orders/{o['id']}/checklists/{cid}", headers=H)).json()
    screen = next(i for i in full["items"] if i["label"] == "Screen assembly")  # client_visible

    await client.patch(f"/api/orders/{o['id']}/checklists/{cid}/items/{screen['id']}", json={"value": "fail"}, headers=H)
    # fail → recommendation
    rec = await client.post(f"/api/orders/{o['id']}/checklists/{cid}/items/{screen['id']}/recommend",
                            json={"unit_price": 80}, headers=H)
    assert rec.status_code == 201, rec.text
    recs = (await client.get(f"/api/orders/{o['id']}/recommendations", headers=H)).json()
    items = recs.get("items", recs) if isinstance(recs, dict) else recs
    assert any("Screen assembly" in (r.get("title") or "") for r in items)

    # client-visible answered field shows on the public track page
    tok = (await client.get(f"/api/orders/{o['id']}", headers=H)).json().get("public_token")
    if tok:
        tr = (await client.get(f"/api/public/track/{tok}")).json()
        labels = [c["label"] for c in tr.get("checklist", [])]
        assert "Screen assembly" in labels
        assert "Battery health" not in labels  # not client_visible


@pytest.mark.asyncio
async def test_required_checklist_blocks_completion(client):
    H, _ = await _register(client)
    t = (await client.post("/api/checklist-templates", json=_tpl_payload(), headers=H)).json()
    o = await _order(client, H)
    await client.post(f"/api/orders/{o['id']}/checklists", json={"template_id": t["id"]}, headers=H)
    # try to close with required checks unfilled → blocked by the completion gate
    r = await client.patch(f"/api/orders/{o['id']}/status", json={"status": "done"}, headers=H)
    assert r.status_code == 409, r.text


@pytest.mark.asyncio
async def test_permission_gate_on_templates(client):
    H, _ = await _register(client)
    # a role that cannot manage templates
    role = (await client.post("/api/staff/roles", json={
        "name": "Tech", "permissions": {"can_manage_checklist_templates": False}}, headers=H)).json()
    email = f"t-{uuid.uuid4().hex[:6]}@t.dev"
    await client.post("/api/staff/employees", json={
        "name": "Tech", "email": email, "password": "pw-test-12345", "role_id": role["id"]}, headers=H)
    tok = (await client.post("/api/auth/token", data={"username": email, "password": "pw-test-12345"})).json()
    TH = {"Authorization": f"Bearer {tok['access_token']}"}
    r = await client.post("/api/checklist-templates", json=_tpl_payload("X"), headers=TH)
    assert r.status_code == 403


# ── a template with nothing to fill in can't be saved (phase 4 bug report) ──────────────
@pytest.mark.asyncio
async def test_template_without_fields_is_refused(client, tenant):
    """Steps saved but every unlabeled item was silently dropped, so applying the template to a
    work order failed with 422 'This template has no fields'. Saving now says what's missing."""
    r = await client.post("/api/checklist-templates", json={
        "name": "Empty intake", "steps": [{"title": "Step 1", "sections": [{"title": "", "items": []}]}]},
        headers=tenant)
    assert r.status_code == 422 and "at least one item" in r.text
    r = await client.post("/api/checklist-templates", json={
        "name": "Blank label", "steps": [{"title": "Intake", "sections": [{"title": "", "items": [{"label": "  "}]}]}]},
        headers=tenant)
    assert r.status_code == 422 and "Intake" in r.text and "label" in r.text

    ok = await client.post("/api/checklist-templates", json={
        "name": "Real intake", "steps": [{"title": "Intake", "sections": [{"title": "", "items": [{"label": "Screen cracked?"}]}]}]},
        headers=tenant)
    assert ok.status_code == 201, ok.text
    r = await client.put(f"/api/checklist-templates/{ok.json()['id']}", json={
        "name": "Real intake", "steps": [{"title": "Intake", "sections": [{"title": "", "items": []}]}]},
        headers=tenant)
    assert r.status_code == 422
