"""Simple document setup: the shop's brand colour reaches every printed document (preview and
print match), receipts carry the terms text, and the receipt preview uses a real sale."""
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
    assert (await client.put("/api/branding", json={"primary_color": "#0F766E"}, headers=H)).status_code == 200
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
    await client.put("/api/branding", json={"primary_color": "red;}</style><script>x()</script>"}, headers=H)
    html = (await client.post("/api/documents/preview", json={"body_html": BODY, "doc_type": "work_order"}, headers=H)).json()["html"]
    assert "tk-branded" not in html and "<script" not in html


@pytest.mark.asyncio
async def test_one_shops_brand_never_reaches_another_shop(client):
    A = await _shop(client, "Shop A")
    B = await _shop(client, "Shop B")
    await _job(client, A); await _job(client, B)
    await client.put("/api/branding", json={"primary_color": "#123456"}, headers=A)
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
