"""144ie — bulk print for clients (labels) and invoices (full docs), exact size."""


async def test_client_labels_render(client, tenant):
    tid = (await client.post("/api/documents/templates", json={
        "name": "Contact card", "doc_type": "client_label", "page_size": "label",
        "page_w_mm": 85, "page_h_mm": 55, "margin_mm": 2,
        "body_html": "<div>{Client Name} · {Client Phone}</div><div>{QR Code}</div>"}, headers=tenant)).json()["id"]
    c1 = (await client.post("/api/clients", json={"name": "Ada Lovelace", "phone": "555-1"}, headers=tenant)).json()["id"]
    c2 = (await client.post("/api/clients", json={"name": "Alan Turing", "phone": "555-2"}, headers=tenant)).json()["id"]
    r = await client.post("/api/documents/render-client-labels", json={
        "template_id": tid, "client_ids": [c1, c2], "copies": 1}, headers=tenant)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 2
    html = body["html"]
    assert "85mm 55mm" in html and "page-break-after" in html
    assert "Ada Lovelace" in html and "Alan Turing" in html


async def test_client_labels_need_ids(client, tenant):
    tid = (await client.post("/api/documents/templates", json={
        "name": "T", "doc_type": "client_label", "body_html": "{Client Name}"}, headers=tenant)).json()["id"]
    r = await client.post("/api/documents/render-client-labels", json={
        "template_id": tid, "client_ids": []}, headers=tenant)
    assert r.status_code == 422


async def test_invoice_batch_one_per_page(client, tenant):
    tid = (await client.post("/api/documents/templates", json={
        "name": "Invoice A4", "doc_type": "invoice", "page_size": "a4",
        "body_html": "<h1>Invoice {Invoice Number}</h1><div>{Invoice Total}</div>"}, headers=tenant)).json()["id"]
    o1 = (await client.post("/api/orders", json={"complaint": "A"}, headers=tenant)).json()["id"]
    o2 = (await client.post("/api/orders", json={"complaint": "B"}, headers=tenant)).json()["id"]
    i1 = (await client.post("/api/finance/invoices", json={"order_id": o1, "subtotal": 40}, headers=tenant)).json()["id"]
    i2 = (await client.post("/api/finance/invoices", json={"order_id": o2, "subtotal": 90}, headers=tenant)).json()["id"]
    r = await client.post("/api/documents/render-invoice-batch", json={
        "template_id": tid, "invoice_ids": [i1, i2]}, headers=tenant)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 2
    html = body["html"]
    assert html.count("Invoice ") >= 2 and "page-break-after" in html
