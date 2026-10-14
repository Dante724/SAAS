"""Build 144s — Orderry-parity variable catalog, row-repeat tables, defaults."""
import pytest


@pytest.mark.asyncio
async def test_full_orderry_variable_catalog(client, tenant):
    d = (await client.get("/api/documents/templates", headers=tenant)).json()
    groups = {g["group"]: g["items"] for g in d["variable_groups"]}
    # every Orderry group is present, in a sensible order
    for grp in ("Company", "Legal entity", "Client", "Payer", "Ticket", "Device",
                "Tables", "Employees", "Finance", "Date", "Other"):
        assert grp in groups, grp
    flat = set(d["variables"])
    # device / asset variables Orderry advertises
    for v in ("{Asset Owner}", "{Asset Warehouse}", "{Asset Modification}", "{Asset Color}",
              "{Asset Year}", "{Asset Cost}", "{Asset State}"):
        assert v in flat, v
    # tables: blocks + per-row product vars + totals
    for v in ("{Products Table}", "{Services Table}", "{All Items Table}",
              "{All Items By Technicians}", "{Row Number}", "{Product Name}", "{Product Qty}",
              "{Product Price Without Taxes}", "{Product Amount}", "{Product Tax Amount}",
              "{Product SKU}", "{Product Code}", "{Product Discount Sum}",
              "{Subtotal}", "{Tax Value}", "{Tax Value Spelled Out}", "{Total Amount}",
              "{Total Qty}", "{Total Discount}"):
        assert v in flat, v
    # employees group
    for v in ("{Current User}", "{Order Assignee Name}", "{Order Assignee Position}",
              "{Order Manager Name}", "{Order Technician Notes}"):
        assert v in flat, v
    # price-tag vars
    for v in ("{Product Title}", "{Product Article}", "{Price}", "{Today Date}"):
        assert v in flat, v


@pytest.mark.asyncio
async def test_default_estimate_and_pricetag_seeded(client, tenant):
    items = (await client.get("/api/documents/templates", headers=tenant)).json()["items"]
    names = {t["name"] for t in items}
    assert "Estimate" in names and "Price tag" in names
    est = next(t for t in items if t["name"] == "Estimate")
    assert "ESTIMATE" in est["body_html"] and "{Product Name}" in est["body_html"]
    tag = next(t for t in items if t["name"] == "Price tag")
    assert tag["page_size"] == "label" and "{Price}" in tag["body_html"]


@pytest.mark.asyncio
async def test_defaults_backfill_for_existing_org_and_no_duplicates(client, tenant):
    # org already has its own template BEFORE it ever lists defaults
    await client.post("/api/documents/templates", json={
        "name": "My Custom Doc", "doc_type": "work_order", "body_html": "hi"}, headers=tenant)
    items = (await client.get("/api/documents/templates", headers=tenant)).json()["items"]
    names = [t["name"] for t in items]
    # the user's own doc is kept AND the Orderry defaults are backfilled alongside it
    assert "My Custom Doc" in names
    assert "Estimate" in names and "Price tag" in names
    # listing again does not duplicate the seeded defaults
    again = (await client.get("/api/documents/templates", headers=tenant)).json()["items"]
    n2 = [t["name"] for t in again]
    assert n2.count("Estimate") == 1 and n2.count("Price tag") == 1


@pytest.mark.asyncio
async def test_products_table_row_repeats_per_line(client, tenant):
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    cid = (await client.post("/api/clients", json={"name": "Rex", "phone": "+15550009"},
                             headers=tenant)).json()["id"]
    oid = (await client.post("/api/orders", json={
        "client_id": cid, "location_id": loc, "complaint": "x",
        "lines": [
            {"line_type": "part", "description": "Screen", "qty": 2, "unit_price": 100},
            {"line_type": "part", "description": "Battery", "qty": 1, "unit_price": 40},
        ]}, headers=tenant)).json()["id"]

    body = ("<table><tbody>"
            "<tr><td>{Row Number}</td><td>{Product Name}</td><td>{Product Qty}</td>"
            "<td>{Product Amount}</td></tr></tbody></table>"
            "<p>Total: {Total Amount} / Qty {Total Qty}</p>")
    tpl = (await client.post("/api/documents/templates", json={
        "name": "Rows", "doc_type": "work_order", "body_html": body}, headers=tenant)).json()["id"]

    res = (await client.get(f"/api/documents/render/{oid}/{tpl}", headers=tenant)).json()
    html = res["html"]
    assert "Screen" in html and "Battery" in html          # both rows rendered
    assert html.count("<tr>") == 2                          # exactly one row per line
    assert "{Product Name}" not in html and "{Row Number}" not in html
    assert "{Total Amount}" not in html and "{Total Qty}" not in html


@pytest.mark.asyncio
async def test_label_docs_excluded_from_order_print_list(client, tenant):
    # seed defaults (adds a doc_type=label "Price tag") then create an order
    (await client.get("/api/documents/templates", headers=tenant)).json()
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    cid = (await client.post("/api/clients", json={"name": "Lee"}, headers=tenant)).json()["id"]
    oid = (await client.post("/api/orders", json={
        "client_id": cid, "location_id": loc, "complaint": "z"}, headers=tenant)).json()["id"]
    # unfiltered list includes the label; order-scoped list must NOT
    alld = (await client.get("/api/documents/templates", headers=tenant)).json()["items"]
    assert any(t["doc_type"] == "label" for t in alld)
    ordd = (await client.get(f"/api/documents/templates?order_id={oid}", headers=tenant)).json()["items"]
    assert all(t["doc_type"] != "label" for t in ordd)
    assert any(t["name"] == "Estimate" for t in ordd)   # real order docs still offered


@pytest.mark.asyncio
async def test_tax_inclusive_price_without_taxes(client, tenant):
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    cid = (await client.post("/api/clients", json={"name": "Ivy"}, headers=tenant)).json()["id"]
    # unit_price 108 already includes 8% tax → ex-tax should be 100.00, tax 8.00
    oid = (await client.post("/api/orders", json={
        "client_id": cid, "location_id": loc, "complaint": "q",
        "lines": [{"line_type": "part", "description": "Board", "qty": 1,
                   "unit_price": 108, "tax_rate": 8, "tax_mode": "inclusive"}]}, headers=tenant)).json()["id"]
    body = ("<table><tbody><tr><td>{Product Price Without Taxes}</td>"
            "<td>{Product Tax Amount}</td></tr></tbody></table>"
            "<p>sub {Subtotal} tax {Tax Value}</p>")
    tpl = (await client.post("/api/documents/templates", json={
        "name": "Inc", "doc_type": "work_order", "body_html": body}, headers=tenant)).json()["id"]
    html = (await client.get(f"/api/documents/render/{oid}/{tpl}", headers=tenant)).json()["html"]
    assert "100.00" in html and "8.00" in html
    assert "sub 100.00" in html and "tax 8.00" in html


@pytest.mark.asyncio
async def test_nested_table_row_not_corrupted(client, tenant):
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    cid = (await client.post("/api/clients", json={"name": "Meg"}, headers=tenant)).json()["id"]
    oid = (await client.post("/api/orders", json={
        "client_id": cid, "location_id": loc, "complaint": "n",
        "lines": [{"line_type": "part", "description": "Fan", "qty": 1, "unit_price": 10}]},
        headers=tenant)).json()["id"]
    # a tbody whose row contains a nested table must be left intact (not repeated/corrupted)
    body = "<table><tbody><tr><td>{Product Name}<table><tbody><tr><td>x</td></tr></tbody></table></td></tr></tbody></table>"
    tpl = (await client.post("/api/documents/templates", json={
        "name": "Nest", "doc_type": "work_order", "body_html": body}, headers=tenant)).json()["id"]
    html = (await client.get(f"/api/documents/render/{oid}/{tpl}", headers=tenant)).json()["html"]
    # nested guard leaves markup untouched; the stray row var is stripped, no product name injected mid-nest
    assert html.count("<table") == 2 and html.count("</table>") == 2
    assert "{Product Name}" not in html


@pytest.mark.asyncio
async def test_real_barcode_and_qr_render_in_order_doc(client, tenant):
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    cid = (await client.post("/api/clients", json={"name": "Bo"}, headers=tenant)).json()["id"]
    oid = (await client.post("/api/orders", json={
        "client_id": cid, "location_id": loc, "complaint": "b"}, headers=tenant)).json()["id"]
    tpl = (await client.post("/api/documents/templates", json={
        "name": "Codes", "doc_type": "work_order",
        "body_html": "BC {Barcode} QR {QR Code}"}, headers=tenant)).json()["id"]
    html = (await client.get(f"/api/documents/render/{oid}/{tpl}", headers=tenant)).json()["html"]
    assert html.count("<svg") == 2                    # a real barcode + a real QR
    assert html.count("<rect") > 25                   # QR has many modules; barcode many bars
    assert "{Barcode}" not in html and "{QR Code}" not in html


@pytest.mark.asyncio
async def test_render_labels_endpoint_unifies_price_tags(client, tenant):
    # a label DocumentTemplate (the unified home for price tags)
    tpl = (await client.post("/api/documents/templates", json={
        "name": "Shelf tag", "doc_type": "price_tag", "page_size": "label",
        "body_html": "<div>{Product Title} — {Price} {Currency}</div>{Barcode}"},
        headers=tenant)).json()["id"]
    p1 = (await client.post("/api/inventory/parts", json={
        "name": "HDMI Cable", "sku": "HD-1", "cost_price": 2, "sell_price": 9.5}, headers=tenant)).json()["id"]
    p2 = (await client.post("/api/inventory/parts", json={
        "name": "USB-C Cable", "sku": "UC-2", "cost_price": 3, "sell_price": 12}, headers=tenant)).json()["id"]

    r = (await client.post("/api/documents/render-labels", json={
        "template_id": tpl, "part_ids": [p1, p2], "copies": 2}, headers=tenant)).json()
    assert r["count"] == 4                              # 2 products × 2 copies
    html = r["html"]
    assert "HDMI Cable" in html and "USB-C Cable" in html
    assert "9.50" in html and "12.00" in html          # each product's own price
    assert html.count("<svg") == 4                      # a real barcode per label
    assert "{Product Title}" not in html and "{Barcode}" not in html


@pytest.mark.asyncio
async def test_label_render_rejects_non_label_missing_parts(client, tenant):
    tpl = (await client.post("/api/documents/templates", json={
        "name": "Tag", "doc_type": "label", "body_html": "{Product Title}"}, headers=tenant)).json()["id"]
    # no parts -> 422
    r = await client.post("/api/documents/render-labels", json={
        "template_id": tpl, "part_ids": [], "copies": 1}, headers=tenant)
    assert r.status_code == 422
    # bad template -> 404
    r2 = await client.post("/api/documents/render-labels", json={
        "template_id": "nope", "part_ids": ["x"], "copies": 1}, headers=tenant)
    assert r2.status_code == 404


@pytest.mark.asyncio
async def test_table_block_variable_expands(client, tenant):
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    cid = (await client.post("/api/clients", json={"name": "Ann"}, headers=tenant)).json()["id"]
    oid = (await client.post("/api/orders", json={
        "client_id": cid, "location_id": loc, "complaint": "y",
        "lines": [{"line_type": "part", "description": "Cable", "qty": 3, "unit_price": 5}]},
        headers=tenant)).json()["id"]
    tpl = (await client.post("/api/documents/templates", json={
        "name": "Block", "doc_type": "work_order", "body_html": "X {Products Table} Y"},
        headers=tenant)).json()["id"]
    html = (await client.get(f"/api/documents/render/{oid}/{tpl}", headers=tenant)).json()["html"]
    assert "<table" in html and "Cable" in html and "{Products Table}" not in html
