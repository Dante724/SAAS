"""144cs — ticking a checklist item from the technician bay bumps the bay card's
checklist progress. Pure integration over existing checklist + my-work endpoints."""
import pytest


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


@pytest.mark.asyncio
async def test_pass_a_check_item_bumps_my_work_progress(client, tenant):
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    loc = await _loc(client, tenant)
    cid = (await client.post("/api/clients", json={"name": "C"}, headers=tenant)).json()["id"]
    o = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x"},
                            headers=tenant)).json()
    # assign me a labour line so the order shows in my bay
    await client.post(f"/api/orders/{o['id']}/lines", json={
        "line_type": "labour", "kind": "service", "description": "Diag",
        "qty": 1, "unit_price": 100, "technician_id": me["id"], "booked_hours": 1.0}, headers=tenant)
    # a 2-item checklist template
    tpl = (await client.post("/api/checklist-templates", json={
        "name": "Intake", "steps": [{"title": "Visual", "sections": [{"title": "Body", "items": [
            {"label": "Screen OK", "field_kind": "check_result"},
            {"label": "Buttons OK", "field_kind": "check_result"}]}]}]}, headers=tenant)).json()
    att = (await client.post(f"/api/orders/{o['id']}/checklists",
                             json={"template_id": tpl["id"]}, headers=tenant)).json()
    clid = att["id"]
    items = (await client.get(f"/api/orders/{o['id']}/checklists/{clid}", headers=tenant)).json()["items"]

    # bay shows 0/2 before
    mw = (await client.get("/api/timeclock/my-work", headers=tenant)).json()
    c0 = next(c for c in mw["orders"] if c["order_id"] == o["id"])
    assert c0["checklist_total"] == 2 and c0["checklist_done"] == 0

    # tech taps Pass on the first item
    r = await client.patch(f"/api/orders/{o['id']}/checklists/{clid}/items/{items[0]['id']}",
                           json={"value": "pass"}, headers=tenant)
    assert r.status_code == 200 and r.json()["item"]["done"] is True

    # bay now reads 1/2
    mw2 = (await client.get("/api/timeclock/my-work", headers=tenant)).json()
    c1 = next(c for c in mw2["orders"] if c["order_id"] == o["id"])
    assert c1["checklist_done"] == 1 and c1["checklist_total"] == 2
