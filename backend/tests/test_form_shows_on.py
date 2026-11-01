"""Forms step 2 — "Shows on" per custom field: Job card / Printed receipt / Customer portal.

Each toggle must change what the output shows: the {Custom Fields} receipt block lists only
show_on_print fields, the public tracking page only show_in_portal fields.
"""
import pytest


async def _loc(client, H):
    b = (await client.get("/api/staff/locations", headers=H)).json()
    return (b.get("items") or b)[0]["id"]


async def _order_with_fields(client, H):
    loc = await _loc(client, H)
    t = (await client.post("/api/forms/types", json={"name": "Phone repair"}, headers=H)).json()
    pw = (await client.post(f"/api/forms/types/{t['id']}/fields", json={"field_label": "Passcode", "field_type": "text"}, headers=H)).json()
    acc = (await client.post(f"/api/forms/types/{t['id']}/fields", json={"field_label": "Accessories", "field_type": "multiselect",
                                                                         "options": ["Charger", "Case"]}, headers=H)).json()
    o = (await client.post("/api/orders", json={"location_id": loc, "type_id": t["id"], "complaint": "screen",
                                                "custom_data": {pw["field_key"]: "4321", acc["field_key"]: ["Charger", "Case"]}},
                           headers=H)).json()
    return t["id"], pw, acc, o


async def _toggle(client, H, f, **flags):
    r = await client.put(f"/api/forms/fields/{f['id']}", json={**f, **flags}, headers=H)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.asyncio
async def test_defaults_and_toggles_round_trip(client, tenant):
    H = tenant
    _, pw, _, _ = await _order_with_fields(client, H)
    assert pw["show_on_job_card"] is True and pw["show_in_portal"] is False and pw["show_on_print"] is False
    f = await _toggle(client, H, pw, show_on_job_card=False, show_in_portal=True)
    assert f["show_on_job_card"] is False and f["show_in_portal"] is True
    # An editor that doesn't send the new flags leaves them alone.
    body = {k: v for k, v in f.items() if k not in ("show_on_job_card", "show_in_portal")}
    f2 = (await client.put(f"/api/forms/fields/{pw['id']}", json=body, headers=H)).json()
    assert f2["show_on_job_card"] is False and f2["show_in_portal"] is True


@pytest.mark.asyncio
async def test_printed_receipt_lists_only_print_fields(client, tenant):
    H = tenant
    _, pw, acc, o = await _order_with_fields(client, H)
    tpl = (await client.post("/api/documents/templates", json={"name": "Intake receipt", "doc_type": "work_order",
                                                                 "body_html": "<p>Details:</p>{Custom Fields}<p>end</p>"}, headers=H)).json()
    html = (await client.get(f"/api/documents/render/{o['id']}/{tpl['id']}", headers=H)).json()["html"]
    assert "Accessories" not in html and "Passcode" not in html and "{Custom Fields}" not in html

    await _toggle(client, H, acc, show_on_print=True)
    html = (await client.get(f"/api/documents/render/{o['id']}/{tpl['id']}", headers=H)).json()["html"]
    assert "Accessories" in html and "Charger, Case" in html
    assert "Passcode" not in html and "4321" not in html   # not ticked for print → stays off the receipt


@pytest.mark.asyncio
async def test_portal_shows_only_portal_fields(client, tenant):
    H = tenant
    _, pw, acc, o = await _order_with_fields(client, H)
    tok = (await client.post(f"/api/orders/{o['id']}/public-link", json={}, headers=H)).json()["token"]
    d = (await client.get(f"/api/public/track/{tok}")).json()
    assert d["custom_fields"] == []
    await _toggle(client, H, acc, show_in_portal=True)
    d = (await client.get(f"/api/public/track/{tok}")).json()
    assert d["custom_fields"] == [{"label": "Accessories", "value": "Charger, Case"}]
    assert "4321" not in str(d)   # the passcode is never exposed unless the shop ticks it


@pytest.mark.asyncio
async def test_hidden_field_never_shows_even_if_ticked(client, tenant):
    H = tenant
    tid, pw, acc, o = await _order_with_fields(client, H)
    await _toggle(client, H, acc, show_in_portal=True)
    lay = (await client.get(f"/api/forms/layout?form=order&type_id={tid}", headers=H)).json()["items"]
    items = [{"ref": i["ref"], "visible": i["ref"] != f"cf:{acc['id']}", "required": i["require"]} for i in lay]
    await client.put("/api/forms/layout", json={"form": "order", "type_id": tid, "items": items}, headers=H)
    tok = (await client.post(f"/api/orders/{o['id']}/public-link", json={}, headers=H)).json()["token"]
    assert (await client.get(f"/api/public/track/{tok}")).json()["custom_fields"] == []
