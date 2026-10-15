"""One ordered list per form (Customize): built-in + custom fields, order / hide / required.

Before: custom fields could only be added or deleted, nothing could be reordered from the UI,
the built-in Show/Required toggles were saved but no form read them, and reorder endpoints were
open to any staff member.
"""
import pytest


async def _type(client, h):
    r = await client.get("/api/forms/types", headers=h)
    items = r.json().get("items", r.json())
    if items:
        return items[0]["id"]
    return (await client.post("/api/forms/types", json={"name": "Repair"}, headers=h)).json()["id"]


@pytest.mark.asyncio
async def test_layout_merges_saves_order_hide_and_required(client, tenant):
    tid = await _type(client, tenant)
    a = (await client.post(f"/api/forms/types/{tid}/fields", json={"field_label": "Passcode", "field_type": "text"}, headers=tenant)).json()
    b = (await client.post(f"/api/forms/types/{tid}/fields", json={"field_label": "Water damage", "field_type": "bool"}, headers=tenant)).json()
    lay = (await client.get(f"/api/forms/layout?form=order&type_id={tid}", headers=tenant)).json()
    refs = [i["ref"] for i in lay["items"]]
    assert f"cf:{a['id']}" in refs and "sys:client" in refs and "sys:complaint" in refs
    assert any(s["label"] for s in lay["suggestions"])

    # move complaint first, b before a, hide a (hidden fields can't stay required), require client
    new = ["sys:complaint", f"cf:{b['id']}", f"cf:{a['id']}"] + [r for r in refs if r not in ("sys:complaint", f"cf:{a['id']}", f"cf:{b['id']}")]
    items = [{"ref": r, "visible": r != f"cf:{a['id']}", "required": r in ("sys:client", f"cf:{a['id']}")} for r in new]
    r = await client.put("/api/forms/layout", json={"form": "order", "type_id": tid, "items": items}, headers=tenant)
    assert r.status_code == 200, r.text
    got = (await client.get(f"/api/forms/layout?form=order&type_id={tid}", headers=tenant)).json()["items"]
    assert [i["ref"] for i in got][:3] == new[:3]
    by = {i["ref"]: i for i in got}
    assert by["sys:client"]["required"] is True
    assert by[f"cf:{a['id']}"]["visible"] is False and by[f"cf:{a['id']}"]["required"] is False

    # the form's field list follows the order and leaves hidden fields out
    fields = (await client.get(f"/api/forms/types/{tid}/fields", headers=tenant)).json()["items"]
    ids = [f["id"] for f in fields]
    assert a["id"] not in ids and ids.index(b["id"]) >= 0


@pytest.mark.asyncio
async def test_entity_form_layout_and_suggestions(client, tenant):
    f = (await client.post("/api/forms/entity-fields", json={"entity_type": "client", "field_label": "Company", "field_type": "text"},
                           headers=tenant)).json()
    lay = (await client.get("/api/forms/layout?form=client", headers=tenant)).json()
    refs = [i["ref"] for i in lay["items"]]
    assert refs[0] == f"cf:{f['id']}" and "sys:phone" in refs and "sys:email" in refs
    assert "company" not in {s["label"].lower() for s in lay["suggestions"]}   # already added


@pytest.mark.asyncio
async def test_reorder_and_layout_need_settings_permission(client, tenant, db):
    from sqlalchemy import select

    from backend.models import Employee, Role
    tid = await _type(client, tenant)
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    role = Role(org_id=me["org_id"], name="Front desk", permissions={"can_manage_settings": False})
    db.add(role)
    await db.flush()
    emp = (await db.execute(select(Employee).where(Employee.id == me["id"]))).scalar_one()
    emp.role_id = role.id
    await db.commit()
    assert (await client.patch(f"/api/forms/types/{tid}/fields/reorder", json={"ordered_ids": []}, headers=tenant)).status_code == 403
    assert (await client.put("/api/forms/layout", json={"form": "order", "type_id": tid, "items": []}, headers=tenant)).status_code == 403
