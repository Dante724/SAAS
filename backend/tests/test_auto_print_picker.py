"""Choose what prints by itself, per document and per moment (Orderry's auto-print checkboxes).

Each document can be switched on or off for: a job is created, a job is closed, a POS sale is
made. Several at once, or none (then it only prints when someone presses Print). The POS
receipt is a template too ("Sales receipt", 80mm), so a sale can print it by itself.
"""
import uuid

import pytest


async def _templates(client, h):
    r = (await client.get("/api/documents/templates", headers=h)).json()
    return r["items"]


async def _asset_label(client, h, **kw):
    r = await client.post("/api/documents/templates", json={
        "name": "Key tag", "doc_type": "label", "page_size": "label", "page_w_mm": 50, "page_h_mm": 30,
        "body_html": "<p>{Asset Brand} {Asset Model} · {Order Number}</p>", **kw}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _job(client, h):
    cid = (await client.post("/api/clients", json={"name": "Robin", "phone": "+15550123"}, headers=h)).json()["id"]
    r = await client.post("/api/orders", json={"complaint": "Screen", "client_id": cid, "asset_brand": "Apple",
                                               "asset_model": "iPhone 14"}, headers=h)
    assert r.status_code in (200, 201), r.text
    return r.json()


async def _printed(client, h, order_id, event):
    r = await client.post(f"/api/documents/auto-print/{order_id}?event={event}", headers=h)
    assert r.status_code == 200, r.text
    return [d["name"] for d in r.json()["docs"]]


async def _sale(client, h):
    r = await client.post("/api/sales", json={"lines": [
        {"kind": "service", "ref_id": None, "description": "Screen protector", "qty": 2, "unit_price": 15}]},
        headers=h)
    assert r.status_code == 201, r.text
    return r.json()


@pytest.mark.asyncio
async def test_switch_a_document_on_for_several_moments_then_off(client, tenant):
    tid = await _asset_label(client, tenant)
    o = await _job(client, tenant)
    assert await _printed(client, tenant, o["id"], "create") == []          # off until switched on

    r = await client.put(f"/api/documents/templates/{tid}/auto-print",
                         json={"events": ["close", "create"]}, headers=tenant)
    assert r.status_code == 200, r.text
    assert r.json()["auto_print_events"] == ["create", "close"]
    assert await _printed(client, tenant, o["id"], "create") == ["Key tag"]
    assert await _printed(client, tenant, o["id"], "close") == ["Key tag"]

    r = await client.put(f"/api/documents/templates/{tid}/auto-print", json={"events": []}, headers=tenant)
    assert r.json()["auto_print_events"] == []
    assert await _printed(client, tenant, o["id"], "create") == []
    assert await _printed(client, tenant, o["id"], "close") == []


@pytest.mark.asyncio
async def test_job_documents_print_at_their_page_size(client, tenant):
    r = await client.post("/api/documents/templates", json={
        "name": "Job receipt", "doc_type": "work_order", "page_size": "receipt80",
        "body_html": "<p>Job {Order Number}</p>", "auto_print_events": ["create"]}, headers=tenant)
    assert r.status_code == 201, r.text
    o = await _job(client, tenant)
    docs = (await client.post(f"/api/documents/auto-print/{o['id']}?event=create", headers=tenant)).json()["docs"]
    assert len(docs) == 1 and o["order_number"] in docs[0]["html"] and "80mm" in docs[0]["html"]


@pytest.mark.asyncio
async def test_what_each_document_can_print_on(client, tenant):
    await _asset_label(client, tenant)
    by_name = {t["name"]: t for t in await _templates(client, tenant)}
    assert by_name["Key tag"]["auto_print_allowed"] == ["create", "close"]
    assert by_name["Sales receipt"]["auto_print_allowed"] == ["create", "close", "sale"]
    assert by_name["Price tag"]["auto_print_allowed"] == []        # a product price tag prints from stock
    assert by_name["Credit note"]["auto_print_allowed"] == []
    # nothing prints by itself out of the box
    assert all(t["auto_print_events"] == [] for t in by_name.values())

    tag = by_name["Price tag"]["id"]
    r = await client.put(f"/api/documents/templates/{tag}/auto-print", json={"events": ["create"]}, headers=tenant)
    assert r.status_code == 422
    key = by_name["Key tag"]["id"]
    r = await client.put(f"/api/documents/templates/{key}/auto-print", json={"events": ["sale"]}, headers=tenant)
    assert r.status_code == 422                                    # a key tag has no job on a POS sale
    r = await client.put(f"/api/documents/templates/{key}/auto-print", json={"events": ["always"]}, headers=tenant)
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_older_single_value_still_works(client, tenant):
    tid = await _asset_label(client, tenant, auto_print_on="create,close")
    t = next(t for t in await _templates(client, tenant) if t["id"] == tid)
    assert t["auto_print_events"] == ["create", "close"]
    o = await _job(client, tenant)
    items = (await client.get(f"/api/auto-print/order-created/{o['id']}", headers=tenant)).json()["items"]
    assert [i["template_id"] for i in items] == [tid]


@pytest.mark.asyncio
async def test_editor_save_without_the_setting_keeps_it(client, tenant):
    tid = await _asset_label(client, tenant, auto_print_events=["create", "close"])
    r = await client.put(f"/api/documents/templates/{tid}", json={
        "name": "Key tag", "doc_type": "label", "page_size": "label", "page_w_mm": 50, "page_h_mm": 30,
        "body_html": "<p>{Asset Brand} {Asset Model}</p>"}, headers=tenant)
    assert r.status_code == 200, r.text
    t = next(t for t in await _templates(client, tenant) if t["id"] == tid)
    assert t["auto_print_events"] == ["create", "close"]


@pytest.mark.asyncio
async def test_a_sale_prints_the_receipt_only_when_switched_on(client, tenant):
    receipt = next(t for t in await _templates(client, tenant) if t["name"] == "Sales receipt")
    s = await _sale(client, tenant)
    r = await client.post(f"/api/documents/auto-print-sale/{s['id']}", headers=tenant)
    assert r.status_code == 200 and r.json() == {"docs": []}

    await client.put(f"/api/documents/templates/{receipt['id']}/auto-print", json={"events": ["sale"]}, headers=tenant)
    docs = (await client.post(f"/api/documents/auto-print-sale/{s['id']}", headers=tenant)).json()["docs"]
    assert [d["name"] for d in docs] == ["Sales receipt"]
    html = docs[0]["html"]
    assert s["sale_number"] in html and "Screen protector" in html and "30.00" in html
    assert "80mm" in html                                          # thermal receipt width
    # a sale-only receipt doesn't print for jobs
    o = await _job(client, tenant)
    assert await _printed(client, tenant, o["id"], "create") == []


@pytest.mark.asyncio
async def test_pos_print_button_uses_the_receipt_template(client, tenant):
    s = await _sale(client, tenant)
    r = await client.get(f"/api/documents/render-sale/{s['id']}", headers=tenant)
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Sales receipt"
    assert s["sale_number"] in r.json()["html"] and "Not paid yet" in r.json()["html"]   # no tender taken
    assert (await client.get("/api/documents/render-sale/nope", headers=tenant)).status_code == 404


@pytest.mark.asyncio
async def test_only_settings_managers_change_it_and_shops_are_separate(client, tenant):
    tid = await _asset_label(client, tenant)
    role = (await client.post("/api/settings/rbac/roles", json={
        "name": "Counter", "permissions": {"can_manage_settings": False}}, headers=tenant)).json()["id"]
    email = f"ctr-{uuid.uuid4().hex[:8]}@t.dev"
    r = await client.post("/api/staff/employees", json={
        "name": "Clerk", "email": email, "password": "pw-test-12345", "role_id": role}, headers=tenant)
    assert r.status_code == 201, r.text
    tok = (await client.post("/api/auth/token", data={"username": email, "password": "pw-test-12345"})).json()
    clerk = {"Authorization": f"Bearer {tok['access_token']}"}
    r = await client.put(f"/api/documents/templates/{tid}/auto-print", json={"events": ["create"]}, headers=clerk)
    assert r.status_code == 403

    other = await client.post("/api/auth/register", json={
        "org_name": "Other", "name": "O", "email": f"o-{uuid.uuid4().hex[:8]}@t.dev", "password": "pw-test-12345"})
    oh = {"Authorization": f"Bearer {other.json()['access_token']}"}
    r = await client.put(f"/api/documents/templates/{tid}/auto-print", json={"events": ["create"]}, headers=oh)
    assert r.status_code == 404
    s = await _sale(client, tenant)
    assert (await client.post(f"/api/documents/auto-print-sale/{s['id']}", headers=oh)).status_code == 404
