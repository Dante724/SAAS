"""Print menu on the work order: printing a job's documents must respect who may see what.

- /render-invoice needs "View invoices" and the job's record scope (it had neither).
- /preview and /print-view respect record scope for a chosen job, and need "View invoices"
  for invoice documents.
- Staff without "View prices" get documents with line prices blanked, like the order screen.
- /render returns an exact-size page, so a receipt or label template doesn't print on A4.
"""
import uuid

import pytest

BODY = ("<p>{Client Name}</p><table><tbody><tr><td>{Product Name}</td><td>{Product Price}</td>"
        "</tr></tbody></table>{All Items Table}")


async def _register(client):
    email = f"pm-{uuid.uuid4().hex[:8]}@t.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Co", "name": "Owner", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _as_role(client, H, **role):
    rid = await client.post("/api/settings/rbac/roles", json={"name": f"R-{uuid.uuid4().hex[:4]}", **role}, headers=H)
    assert rid.status_code == 201, rid.text
    email = f"tm-{uuid.uuid4().hex[:8]}@t.dev"
    r = await client.post("/api/staff/employees", json={
        "name": "Tm", "email": email, "password": "pw-test-12345", "role_id": rid.json()["id"]}, headers=H)
    assert r.status_code == 201, r.text
    tok = await client.post("/api/auth/token", data={"username": email, "password": "pw-test-12345"})
    return {"Authorization": f"Bearer {tok.json()['access_token']}"}


async def _job(client, H, *, assign_to_owner=False):
    cid = (await client.post("/api/clients", json={"name": "Ravi Kumar"}, headers=H)).json()["id"]
    body = {"client_id": cid, "complaint": "screen"}
    if assign_to_owner:
        body["assigned_to_id"] = (await client.get("/api/auth/me", headers=H)).json()["id"]
    oid = (await client.post("/api/orders", json=body, headers=H)).json()["id"]
    r = await client.post(f"/api/orders/{oid}/lines", json={
        "line_type": "labour", "kind": "service", "description": "Screen swap", "qty": 1,
        "unit_price": 123.45}, headers=H)
    assert r.status_code in (200, 201), r.text
    return oid


async def _template(client, H, doc_type="estimate", **kw):
    r = await client.post("/api/documents/templates", json={
        "name": f"T-{doc_type}", "doc_type": doc_type, "body_html": BODY, **kw}, headers=H)
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ── invoices ─────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_render_invoice_needs_view_invoices(client):
    H = await _register(client)
    oid = await _job(client, H)
    tid = await _template(client, H, "invoice")
    inv = (await client.post("/api/finance/invoices", json={"order_id": oid, "subtotal": 50}, headers=H)).json()["id"]
    assert (await client.get(f"/api/documents/render-invoice/{inv}/{tid}", headers=H)).status_code == 200
    T = await _as_role(client, H, permissions={"can_view_invoices": False})
    r = await client.get(f"/api/documents/render-invoice/{inv}/{tid}", headers=T)
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_render_invoice_respects_the_jobs_record_scope(client):
    H = await _register(client)
    oid = await _job(client, H, assign_to_owner=True)
    tid = await _template(client, H, "invoice")
    inv = (await client.post("/api/finance/invoices", json={"order_id": oid, "subtotal": 50}, headers=H)).json()["id"]
    T = await _as_role(client, H, order_scope="own", permissions={"can_view_invoices": True})
    r = await client.get(f"/api/documents/render-invoice/{inv}/{tid}", headers=T)
    assert r.status_code == 403


# ── preview / print-view ─────────────────────────────────────────────────────
@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/documents/preview", "/api/documents/print-view"])
async def test_preview_and_print_view_respect_record_scope(client, path):
    H = await _register(client)
    oid = await _job(client, H, assign_to_owner=True)
    T = await _as_role(client, H, order_scope="own")
    r = await client.post(path, json={"body_html": BODY, "doc_type": "estimate", "order_id": oid}, headers=T)
    assert r.status_code == 403
    assert (await client.post(path, json={"body_html": BODY, "doc_type": "estimate", "order_id": oid},
                              headers=H)).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/documents/preview", "/api/documents/print-view"])
async def test_preview_and_print_view_invoices_need_view_invoices(client, path):
    H = await _register(client)
    oid = await _job(client, H)
    await client.post("/api/finance/invoices", json={"order_id": oid, "subtotal": 50}, headers=H)
    T = await _as_role(client, H, permissions={"can_view_invoices": False})
    r = await client.post(path, json={"body_html": BODY, "doc_type": "invoice"}, headers=T)
    assert r.status_code == 403


# ── prices ───────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_render_blanks_line_prices_for_staff_without_view_prices(client):
    H = await _register(client)
    oid = await _job(client, H)
    tid = await _template(client, H, "estimate")
    owner = (await client.get(f"/api/documents/render/{oid}/{tid}", headers=H)).json()["html"]
    assert "123.45" in owner and "Screen swap" in owner

    T = await _as_role(client, H, permissions={"can_view_item_prices": False})
    r = await client.get(f"/api/documents/render/{oid}/{tid}", headers=T)
    assert r.status_code == 200, r.text
    html = r.json()["html"]
    assert "Screen swap" in html and "Ravi Kumar" in html      # the job still prints
    assert "123.45" not in html                                  # but not its line prices


@pytest.mark.asyncio
async def test_print_view_blanks_line_prices_for_staff_without_view_prices(client):
    H = await _register(client)
    oid = await _job(client, H)
    T = await _as_role(client, H, permissions={"can_view_item_prices": False})
    r = await client.post("/api/documents/print-view", json={
        "body_html": BODY, "doc_type": "estimate", "order_id": oid}, headers=T)
    assert r.status_code == 200, r.text
    assert "Screen swap" in r.json()["html"] and "123.45" not in r.json()["html"]


# ── exact size ───────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_render_prints_at_the_templates_exact_size(client):
    H = await _register(client)
    oid = await _job(client, H)
    tid = await _template(client, H, "receipt", page_size="receipt80")
    r = await client.get(f"/api/documents/render/{oid}/{tid}", headers=H)
    assert r.status_code == 200, r.text
    html = r.json()["html"]
    assert "@page{size:80mm" in html and "Ravi Kumar" in html


@pytest.mark.asyncio
async def test_render_invoice_prints_at_the_templates_exact_size(client):
    H = await _register(client)
    oid = await _job(client, H)
    tid = await _template(client, H, "invoice", page_size="receipt80")
    inv = (await client.post("/api/finance/invoices", json={"order_id": oid, "subtotal": 50}, headers=H)).json()["id"]
    r = await client.get(f"/api/documents/render-invoice/{inv}/{tid}", headers=H)
    assert r.status_code == 200, r.text
    assert "@page{size:80mm" in r.json()["html"]


# ── review fixes ─────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_font_family_cannot_break_out_of_the_style_block(client):
    H = await _register(client)
    oid = await _job(client, H)
    tid = await _template(client, H, "estimate", font_family="x}</style><img src=//evil.test/a>")
    html = (await client.get(f"/api/documents/render/{oid}/{tid}", headers=H)).json()["html"]
    assert "evil.test" not in html and html.count("</style>") == 1
    r = await client.post("/api/documents/print-view", json={
        "body_html": BODY, "doc_type": "estimate", "order_id": oid,
        "font_family": "a}</style><script>x</script>"}, headers=H)
    assert r.status_code in (200, 422)
    if r.status_code == 200:
        assert "<script" not in r.json()["html"]


@pytest.mark.asyncio
async def test_doc_level_line_price_is_hidden_too(client):
    H = await _register(client)
    oid = await _job(client, H)
    r = await client.post("/api/documents/templates", json={
        "name": "P", "doc_type": "estimate", "body_html": "<p>{Client Name} {Price}</p>"}, headers=H)
    T = await _as_role(client, H, permissions={"can_view_item_prices": False})
    html = (await client.get(f"/api/documents/render/{oid}/{r.json()['id']}", headers=T)).json()["html"]
    assert "Ravi Kumar" in html and "123.45" not in html


@pytest.mark.asyncio
async def test_invoice_line_prices_hidden_without_view_prices(client):
    H = await _register(client)
    oid = await _job(client, H)
    tid = await _template(client, H, "invoice")
    inv = (await client.post("/api/finance/invoices", json={"order_id": oid}, headers=H)).json()["id"]
    owner = (await client.get(f"/api/documents/render-invoice/{inv}/{tid}", headers=H)).json()["html"]
    assert "123.45" in owner
    T = await _as_role(client, H, permissions={"can_view_invoices": True, "can_view_item_prices": False})
    r = await client.get(f"/api/documents/render-invoice/{inv}/{tid}", headers=T)
    assert r.status_code == 200 and "123.45" not in r.json()["html"]


@pytest.mark.asyncio
async def test_invoice_batch_respects_record_scope(client):
    H = await _register(client)
    oid = await _job(client, H, assign_to_owner=True)
    tid = await _template(client, H, "invoice")
    inv = (await client.post("/api/finance/invoices", json={"order_id": oid, "subtotal": 50}, headers=H)).json()["id"]
    T = await _as_role(client, H, order_scope="own", permissions={"can_view_invoices": True})
    r = await client.post("/api/documents/render-invoice-batch", json={"template_id": tid, "invoice_ids": [inv]}, headers=T)
    assert r.status_code == 403
