"""144go — smart checklist upsell: a Fail auto-drafts a recommendation, is
idempotent, and retracts if the value stops triggering (before the customer sees it).
"""
import pytest


async def _loc(client, H):
    r = await client.get("/api/staff/locations", headers=H)
    b = r.json()
    return (b.get("items") or b)[0]["id"]


async def _setup(client, H):
    # Template: one check_result item wired to auto-upsell a $59 battery on Fail.
    tpl = await client.post("/api/checklist-templates", json={
        "name": "Diagnostics", "scope": "all",
        "steps": [{"title": "Diagnostics", "sections": [{"title": "Health", "items": [
            {"label": "Battery health", "field_kind": "check_result",
             "upsell_kind": "custom", "upsell_price": 59.0, "upsell_comparator": "fail"},
        ]}]}]}, headers=H)
    assert tpl.status_code == 201, tpl.text
    tid = tpl.json()["id"]
    loc_id = await _loc(client, H)
    o = (await client.post("/api/orders", json={"location_id": loc_id, "complaint": "x"}, headers=H)).json()
    cl = await client.post(f"/api/orders/{o['id']}/checklists", json={"template_id": tid}, headers=H)
    assert cl.status_code == 201, cl.text
    cid = cl.json()["id"]
    # fetch the item id
    got = await client.get(f"/api/orders/{o['id']}/checklists/{cid}", headers=H)
    iid = got.json()["items"][0]["id"]
    return o["id"], cid, iid


async def _recs(client, H, oid):
    r = await client.get(f"/api/orders/{oid}/recommendations", headers=H)
    return r.json().get("items", r.json()) if isinstance(r.json(), dict) else r.json()


@pytest.mark.asyncio
async def test_fail_autodrafts_and_is_idempotent(client, tenant):
    H = tenant
    oid, cid, iid = await _setup(client, H)

    # Pass → nothing drafted.
    await client.patch(f"/api/orders/{oid}/checklists/{cid}/items/{iid}", json={"value": "pass"}, headers=H)
    recs = await _recs(client, H, oid)
    assert not [r for r in recs if r.get("source") == "checklist"], recs

    # Fail → one checklist-sourced draft, priced from config.
    await client.patch(f"/api/orders/{oid}/checklists/{cid}/items/{iid}", json={"value": "fail"}, headers=H)
    recs = [r for r in await _recs(client, H, oid) if r.get("source") == "checklist"]
    assert len(recs) == 1, recs
    assert recs[0]["unit_price"] == 59.0
    assert recs[0]["status"] == "draft"

    # Fail again → still exactly one (idempotent).
    await client.patch(f"/api/orders/{oid}/checklists/{cid}/items/{iid}", json={"value": "fail"}, headers=H)
    recs = [r for r in await _recs(client, H, oid) if r.get("source") == "checklist"]
    assert len(recs) == 1, recs


@pytest.mark.asyncio
async def test_retracts_when_no_longer_failing(client, tenant):
    H = tenant
    oid, cid, iid = await _setup(client, H)
    await client.patch(f"/api/orders/{oid}/checklists/{cid}/items/{iid}", json={"value": "fail"}, headers=H)
    assert len([r for r in await _recs(client, H, oid) if r.get("source") == "checklist"]) == 1
    # Corrected to pass before the customer saw it → the draft retracts.
    await client.patch(f"/api/orders/{oid}/checklists/{cid}/items/{iid}", json={"value": "pass"}, headers=H)
    assert len([r for r in await _recs(client, H, oid) if r.get("source") == "checklist"]) == 0
