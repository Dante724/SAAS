"""144ib — bulk label print renders one label per page at exact size, product×copies."""


async def test_bulk_labels_exact_size_and_count(client, tenant):
    tid = (await client.post("/api/documents/templates", json={
        "name": "Tag 40x30", "doc_type": "label", "page_size": "label",
        "page_w_mm": 40, "page_h_mm": 30, "margin_mm": 1,
        "body_html": "<div>{Product Title} {Price}{Currency}</div><div>{Barcode}</div>"}, headers=tenant)).json()["id"]
    p1 = (await client.post("/api/catalog/products", json={"name": "Filter A", "sell_price": 9}, headers=tenant)).json()["id"]
    p2 = (await client.post("/api/catalog/products", json={"name": "Filter B", "sell_price": 12}, headers=tenant)).json()["id"]
    r = await client.post("/api/documents/render-labels", json={
        "template_id": tid, "part_ids": [p1, p2], "copies": 3}, headers=tenant)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 6                      # 2 products × 3 copies
    html = body["html"]
    assert "40mm 30mm" in html and "margin:1mm" in html   # exact size honoured
    assert "Filter A" in html and "Filter B" in html
    assert "page-break-after" in html               # one label per physical page
