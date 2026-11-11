"""Simple document setup: the shop's brand colour reaches every printed document (preview and
print match), receipts carry the terms text, and the receipt preview uses a real sale."""
import re as _re
import uuid

import pytest


async def _shop(client, name="Brand Co"):
    r = await client.post("/api/auth/register", json={
        "org_name": name, "name": "Admin", "email": f"a-{uuid.uuid4().hex[:8]}@test.dev", "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _job(client, H):
    loc = (await client.get("/api/staff/locations", headers=H)).json()["items"][0]["id"]
    cid = (await client.post("/api/clients", json={"name": "Rex", "phone": "+15550009"}, headers=H)).json()["id"]
    return (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x"}, headers=H)).json()["id"]


BODY = "<h1>Invoice</h1><p>{Client Name}</p>"


@pytest.mark.asyncio
async def test_no_brand_colour_means_the_page_is_unchanged(client):
    H = await _shop(client)
    await _job(client, H)
    html = (await client.post("/api/documents/preview", json={"body_html": BODY, "doc_type": "work_order"}, headers=H)).json()["html"]
    assert "tk-branded" not in html
    assert html.startswith("<h1>Invoice</h1>")


@pytest.mark.asyncio
async def test_brand_colour_styles_preview_and_print_the_same_way(client):
    H = await _shop(client)
    oid = await _job(client, H)
    assert (await client.put("/api/branding", json={"primary_color": "#0F766E", "doc_color_on": True}, headers=H)).status_code == 200
    prev = (await client.post("/api/documents/preview", json={"body_html": BODY, "doc_type": "work_order"}, headers=H)).json()["html"]
    assert ".tk-branded h1" in prev and "#0F766E" in prev
    assert '<div class="tk-branded">' in prev and "Rex" in prev
    printed = (await client.post("/api/documents/print-view", json={"body_html": BODY, "doc_type": "work_order", "order_id": oid}, headers=H)).text
    assert ".tk-branded h1" in printed and "#0F766E" in printed
    # every kind of document: invoices too
    inv = (await client.post("/api/documents/preview", json={"body_html": BODY, "doc_type": "invoice"}, headers=H)).json()["html"]
    assert "#0F766E" in inv


@pytest.mark.asyncio
async def test_a_colour_that_is_not_a_hex_colour_is_never_written_into_css(client):
    H = await _shop(client)
    await _job(client, H)
    await client.put("/api/branding", json={"primary_color": "red;}</style><script>x()</script>", "doc_color_on": True}, headers=H)
    html = (await client.post("/api/documents/preview", json={"body_html": BODY, "doc_type": "work_order"}, headers=H)).json()["html"]
    assert "tk-branded" not in html and "<script" not in html


@pytest.mark.asyncio
async def test_one_shops_brand_never_reaches_another_shop(client):
    A = await _shop(client, "Shop A")
    B = await _shop(client, "Shop B")
    await _job(client, A)
    await _job(client, B)
    await client.put("/api/branding", json={"primary_color": "#123456", "doc_color_on": True}, headers=A)
    html_b = (await client.post("/api/documents/preview", json={"body_html": BODY, "doc_type": "work_order"}, headers=B)).json()["html"]
    assert "#123456" not in html_b and "tk-branded" not in html_b


@pytest.mark.asyncio
async def test_receipt_preview_uses_a_sale_and_shows_the_terms(client):
    H = await _shop(client)
    await client.put("/api/staff/company", json={"company_note": "Returns within 14 days."}, headers=H)
    till = (await client.post("/api/cash/accounts", json={"name": "Till", "payment_option": "cash"}, headers=H)).json()["id"]
    r = await client.post("/api/sales", json={"account_id": till, "lines": [
        {"kind": "service", "description": "Screen fit", "qty": 1, "unit_price": 40}]}, headers=H)
    assert r.status_code in (200, 201), r.text
    body = "<p>{Sale Number}</p><table><tbody><tr><td>{Product Name}</td></tr></tbody></table><p>{Company Note}</p>"
    out = (await client.post("/api/documents/preview", json={"body_html": body, "doc_type": "receipt"}, headers=H)).json()
    assert "Screen fit" in out["html"]
    assert "Returns within 14 days." in out["html"]
    assert out["sample"]                                  # the sale it used


@pytest.mark.asyncio
async def test_receipt_preview_falls_back_to_a_job_when_there_are_no_sales(client):
    H = await _shop(client)
    await _job(client, H)
    out = (await client.post("/api/documents/preview", json={"body_html": "<p>{Client Name}</p>", "doc_type": "receipt"}, headers=H)).json()
    assert "Rex" in out["html"]


# ── review M3: the brand colour on documents is opt-in ─────────────────────────────────────────
@pytest.mark.asyncio
async def test_a_portal_brand_colour_alone_leaves_documents_exactly_as_before(client):
    H = await _shop(client)
    oid = await _job(client, H)
    before_prev = (await client.post("/api/documents/preview", json={"body_html": BODY, "doc_type": "work_order"}, headers=H)).json()["html"]
    before_print = (await client.post("/api/documents/print-view", json={"body_html": BODY, "doc_type": "work_order", "order_id": oid}, headers=H)).text
    # a shop that set its white-label colour for the portal (documents switch never turned on)
    assert (await client.put("/api/branding", json={"primary_color": "#0F766E"}, headers=H)).status_code == 200
    after_prev = (await client.post("/api/documents/preview", json={"body_html": BODY, "doc_type": "work_order"}, headers=H)).json()["html"]
    after_print = (await client.post("/api/documents/print-view", json={"body_html": BODY, "doc_type": "work_order", "order_id": oid}, headers=H)).text
    assert after_prev == before_prev
    assert after_print == before_print
    b = (await client.get("/api/branding", headers=H)).json()
    assert b["primary_color"] == "#0F766E" and b["doc_color_on"] is False


@pytest.mark.asyncio
async def test_documents_switch_is_per_shop_and_turning_it_off_keeps_the_portal_colour(client):
    A = await _shop(client, "Shop A")
    B = await _shop(client, "Shop B")
    await client.put("/api/branding", json={"primary_color": "#0F766E", "doc_color_on": True}, headers=A)
    await client.put("/api/branding", json={"primary_color": "#111111"}, headers=B)
    assert (await client.get("/api/branding", headers=A)).json()["doc_color_on"] is True
    assert (await client.get("/api/branding", headers=B)).json()["doc_color_on"] is False
    await client.put("/api/branding", json={"doc_color_on": False}, headers=A)
    a = (await client.get("/api/branding", headers=A)).json()
    assert a["doc_color_on"] is False and a["primary_color"] == "#0F766E"


# ── review L4: labels stay unbranded ───────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_auto_printed_labels_are_never_branded(client):
    H = await _shop(client)
    await client.put("/api/branding", json={"primary_color": "#0F766E", "doc_color_on": True}, headers=H)
    r = await client.post("/api/documents/templates", json={
        "name": "Asset sticker", "doc_type": "label", "page_size": "label", "page_w_mm": 50, "page_h_mm": 30,
        "body_html": "<h1>{Asset Brand}</h1>", "auto_print_on": "create"}, headers=H)
    assert r.status_code == 201, r.text
    cid = (await client.post("/api/clients", json={"name": "Robin", "phone": "+15550123"}, headers=H)).json()["id"]
    o = (await client.post("/api/orders", json={"complaint": "Screen", "client_id": cid, "asset_brand": "Apple"}, headers=H)).json()
    docs = (await client.post(f"/api/documents/auto-print/{o['id']}?event=create", headers=H)).json()["docs"]
    assert docs and all("tk-branded" not in d["html"] for d in docs)


# ── review: the standard layouts fill every field (no leftover {…} on the page) ────────────────
LEFT = _re.compile(r"\{[A-Z][^{}]*\}")


async def _preset(client, H, key, doc_type):
    return (await client.post("/api/documents/ai-generate", json={"prompt": "", "doc_type": doc_type, "preset": key}, headers=H)).json()["body_html"]


@pytest.mark.asyncio
async def test_detailed_invoice_preview_has_no_leftover_fields(client):
    H = await _shop(client)
    await _job(client, H)
    cid = (await client.post("/api/clients", json={"name": "Zed Corp"}, headers=H)).json()["id"]
    r = await client.post("/api/finance/invoices", json={"client_id": cid, "lines": [
        {"kind": "custom", "description": "Diagnostics", "qty": 1, "unit_price": 40}]}, headers=H)
    assert r.status_code in (200, 201), r.text
    body = await _preset(client, H, "invoice_detailed", "invoice")
    html = (await client.post("/api/documents/preview", json={"body_html": body, "doc_type": "invoice"}, headers=H)).json()["html"]
    assert "Diagnostics" in html
    assert LEFT.findall(html) == []
    assert "40" in html                              # the totals are real numbers


@pytest.mark.asyncio
async def test_quote_and_work_order_previews_have_no_leftover_fields(client):
    H = await _shop(client)
    await _job(client, H)
    for key, kind in (("estimate_standard", "estimate"), ("work_order_full", "work_order")):
        body = await _preset(client, H, key, kind)
        html = (await client.post("/api/documents/preview", json={"body_html": body, "doc_type": kind}, headers=H)).json()["html"]
        assert LEFT.findall(html) == [], key


@pytest.mark.asyncio
async def test_receipt_previews_have_no_leftover_fields(client):
    H = await _shop(client)
    till = (await client.post("/api/cash/accounts", json={"name": "Till", "payment_option": "cash"}, headers=H)).json()["id"]
    await client.post("/api/sales", json={"account_id": till, "lines": [{"kind": "service", "description": "Fit", "qty": 1, "unit_price": 40}]}, headers=H)
    from backend.routers.documents import DEFAULT_SALE_RECEIPT_HTML
    for body in (await _preset(client, H, "receipt_simple", "receipt"), DEFAULT_SALE_RECEIPT_HTML):
        html = (await client.post("/api/documents/preview", json={"body_html": body, "doc_type": "receipt"}, headers=H)).json()["html"]
        assert LEFT.findall(html) == [], LEFT.findall(html)
