"""Forms step 2 — three required levels: Optional / At intake / Before closing.

"Before closing" fields may be empty when the work order is created but must be filled before it
moves to a closed/done status; the 409 names what's missing. "At intake" is enforced on create.
"""
import pytest


async def _loc(client, H):
    b = (await client.get("/api/staff/locations", headers=H)).json()
    return (b.get("items") or b)[0]["id"]


async def _closing_status(client, H):
    b = (await client.get("/api/statuses", headers=H)).json()
    items = b.get("items", b)
    st = next(s for s in items if s.get("is_terminal") and not s.get("is_estimate")
              and (s.get("record_type") or "work_order") == "work_order" and s.get("status_group") != "dropped")
    return st["key"]


async def _setup(client, H, name="Repair"):
    t = (await client.post("/api/forms/types", json={"name": name}, headers=H)).json()
    pw = (await client.post(f"/api/forms/types/{t['id']}/fields", json={"field_label": "Passcode", "field_type": "text"}, headers=H)).json()
    bk = (await client.post(f"/api/forms/types/{t['id']}/fields", json={"field_label": "Data backed up", "field_type": "bool"}, headers=H)).json()
    return t["id"], pw, bk


async def _save(client, H, tid, levels):
    lay = (await client.get(f"/api/forms/layout?form=order&type_id={tid}", headers=H)).json()["items"]
    items = [{"ref": i["ref"], "visible": i["visible"], "required": levels.get(i["ref"], i["require"])} for i in lay]
    r = await client.put("/api/forms/layout", json={"form": "order", "type_id": tid, "items": items}, headers=H)
    assert r.status_code == 200, r.text
    return {i["ref"]: i for i in r.json()["items"]}


@pytest.mark.asyncio
async def test_before_closing_blocks_close_until_filled(client, tenant):
    H = tenant
    loc = await _loc(client, H)
    done = await _closing_status(client, H)
    tid, pw, bk = await _setup(client, H)
    by = await _save(client, H, tid, {f"cf:{pw['id']}": "close", f"cf:{bk['id']}": "close", "sys:assignee": "close"})
    assert by[f"cf:{pw['id']}"]["require"] == "close" and by[f"cf:{pw['id']}"]["required"] is False
    assert by["sys:assignee"]["require"] == "close"

    # Empty at intake is fine…
    o = await client.post("/api/orders", json={"location_id": loc, "type_id": tid, "complaint": "cracked"}, headers=H)
    assert o.status_code == 201, o.text
    oid = o.json()["id"]
    # …but closing names every missing field.
    r = await client.patch(f"/api/orders/{oid}/status", json={"status": done}, headers=H)
    assert r.status_code == 409, r.text
    msg = r.json()["detail"]
    assert "Passcode" in msg and "Data backed up" in msg and "Technician" in msg

    # Fill the fields on the job card (an unchecked yes/no still blocks), assign a tech.
    r = await client.patch(f"/api/orders/{oid}/custom-data", json={"custom_data": {pw["field_key"]: "1234", bk["field_key"]: False}}, headers=H)
    assert r.status_code == 200, r.text
    me = (await client.get("/api/auth/me", headers=H)).json()
    await client.patch(f"/api/orders/{oid}/assign", json={"assigned_to_id": me["id"]}, headers=H)
    r = await client.patch(f"/api/orders/{oid}/status", json={"status": done}, headers=H)
    assert r.status_code == 409 and "Data backed up" in r.json()["detail"] and "Passcode" not in r.json()["detail"]

    await client.patch(f"/api/orders/{oid}/custom-data", json={"custom_data": {bk["field_key"]: True}}, headers=H)
    r = await client.patch(f"/api/orders/{oid}/status", json={"status": done}, headers=H)
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_hidden_or_optional_fields_never_block_close(client, tenant):
    H = tenant
    loc = await _loc(client, H)
    done = await _closing_status(client, H)
    tid, pw, _ = await _setup(client, H, "Diagnostics")
    lay = (await client.get(f"/api/forms/layout?form=order&type_id={tid}", headers=H)).json()["items"]
    # Hiding a field drops its requirement.
    items = [{"ref": i["ref"], "visible": i["ref"] != f"cf:{pw['id']}", "required": "close" if i["ref"] == f"cf:{pw['id']}" else ""} for i in lay]
    r = await client.put("/api/forms/layout", json={"form": "order", "type_id": tid, "items": items}, headers=H)
    assert {i["ref"]: i for i in r.json()["items"]}[f"cf:{pw['id']}"]["require"] == ""
    o = (await client.post("/api/orders", json={"location_id": loc, "type_id": tid, "complaint": "x"}, headers=H)).json()
    r = await client.patch(f"/api/orders/{o['id']}/status", json={"status": done}, headers=H)
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_at_intake_enforced_on_create(client, tenant):
    H = tenant
    loc = await _loc(client, H)
    tid, pw, _ = await _setup(client, H, "Warranty")
    await _save(client, H, tid, {f"cf:{pw['id']}": "intake", "sys:complaint": "intake"})
    r = await client.post("/api/orders", json={"location_id": loc, "type_id": tid}, headers=H)
    assert r.status_code == 422 and "Passcode" in r.text and "complaint" in r.text.lower()
    r = await client.post("/api/orders", json={"location_id": loc, "type_id": tid, "complaint": "x",
                                               "custom_data": {pw["field_key"]: "0000"}}, headers=H)
    assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_close_level_only_for_work_orders(client, tenant):
    H = tenant
    f = (await client.post("/api/forms/entity-fields", json={"entity_type": "client", "field_label": "Company", "field_type": "text"},
                           headers=H)).json()
    r = await client.put("/api/forms/layout", json={"form": "client", "items": [{"ref": f"cf:{f['id']}", "visible": True, "required": "close"}]}, headers=H)
    assert r.json()["items"][0]["require"] == ""


@pytest.mark.asyncio
async def test_custom_data_patch_keeps_only_type_fields_and_locks(client, tenant):
    H = tenant
    loc = await _loc(client, H)
    t = (await client.post("/api/forms/types", json={"name": "Lockable"}, headers=H)).json()
    lk = (await client.post(f"/api/forms/types/{t['id']}/fields", json={"field_label": "Serial at intake", "field_type": "text",
                                                                        "lock_after_create": True}, headers=H)).json()
    o = (await client.post("/api/orders", json={"location_id": loc, "type_id": t["id"], "complaint": "x",
                                                "custom_data": {lk["field_key"]: "A1"}}, headers=H)).json()
    r = await client.patch(f"/api/orders/{o['id']}/custom-data", json={"custom_data": {lk["field_key"]: "B2", "junk": "x"}}, headers=H)
    assert r.json()["custom_data"] == {lk["field_key"]: "A1"}
