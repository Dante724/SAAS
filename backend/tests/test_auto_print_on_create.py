"""Auto-print an asset label when a job is created (Orderry, Jul 2026).

DocumentTemplate.auto_print_on was stored but unused. POST /api/documents/auto-print/{order}
renders every template set to print on the event, so the desktop and Go can open the print
dialog right after creating a job.
"""
import pytest


async def _template(client, h, **kw):
    body = {"name": "Asset sticker", "doc_type": "label", "page_size": "label", "page_w_mm": 50, "page_h_mm": 30,
            "body_html": "<p>{Asset Brand} {Asset Model} · {Order Number}</p>", **kw}
    r = await client.post("/api/documents/templates", json=body, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _job(client, h):
    cid = (await client.post("/api/clients", json={"name": "Robin", "phone": "+15550123"}, headers=h)).json()["id"]
    r = await client.post("/api/orders", json={"complaint": "Screen", "client_id": cid, "asset_brand": "Apple",
                                               "asset_model": "iPhone 14"}, headers=h)
    assert r.status_code in (200, 201), r.text
    return r.json()


@pytest.mark.asyncio
async def test_nothing_set_prints_nothing(client, tenant):
    await _template(client, tenant)              # not set to auto-print
    o = await _job(client, tenant)
    r = await client.post(f"/api/documents/auto-print/{o['id']}?event=create", headers=tenant)
    assert r.status_code == 200 and r.json() == {"docs": []}


@pytest.mark.asyncio
async def test_label_set_to_print_on_create_renders_for_the_job(client, tenant):
    tid = await _template(client, tenant, auto_print_on="create")
    o = await _job(client, tenant)
    r = await client.post(f"/api/documents/auto-print/{o['id']}?event=create", headers=tenant)
    assert r.status_code == 200, r.text
    docs = r.json()["docs"]
    assert [d["template_id"] for d in docs] == [tid]
    html = docs[0]["html"]
    assert "Apple" in html and "iPhone 14" in html and o["order_number"] in html
    assert "50mm 30mm" in html                     # printed at the label's exact size
    # it doesn't fire for the other event
    r = await client.post(f"/api/documents/auto-print/{o['id']}?event=close", headers=tenant)
    assert r.json()["docs"] == []


@pytest.mark.asyncio
async def test_editor_save_keeps_and_clears_the_setting(client, tenant):
    tid = await _template(client, tenant, auto_print_on="create")
    rows = (await client.get("/api/documents/templates", headers=tenant)).json()
    rows = rows.get("items", rows) if isinstance(rows, dict) else rows
    assert next(t for t in rows if t["id"] == tid)["auto_print_on"] == "create"
    r = await client.put(f"/api/documents/templates/{tid}", json={
        "name": "Asset sticker", "doc_type": "label", "body_html": "<p>x</p>", "auto_print_on": ""}, headers=tenant)
    assert r.status_code == 200, r.text
    o = await _job(client, tenant)
    assert (await client.post(f"/api/documents/auto-print/{o['id']}", headers=tenant)).json()["docs"] == []


@pytest.mark.asyncio
async def test_bad_value_and_event_rejected(client, tenant):
    r = await client.post("/api/documents/templates", json={
        "name": "x", "doc_type": "label", "auto_print_on": "always"}, headers=tenant)
    assert r.status_code == 422
    o = await _job(client, tenant)
    assert (await client.post(f"/api/documents/auto-print/{o['id']}?event=never", headers=tenant)).status_code == 422


@pytest.mark.asyncio
async def test_other_shops_job_is_404(client, tenant):
    await _template(client, tenant, auto_print_on="create")
    r = await client.post("/api/documents/auto-print/not-a-real-id", headers=tenant)
    assert r.status_code == 404
