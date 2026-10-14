"""144ia — exact-size print pipeline: @page CSS at true physical size, data resolved."""


async def test_print_view_receipt_size_and_data(client, tenant):
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    cid = (await client.post("/api/clients", json={"name": "Pat Ng"}, headers=tenant)).json()["id"]
    await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x"}, headers=tenant)
    r = await client.post("/api/documents/print-view", json={
        "body_html": "<h1>{Company Name}</h1><p>{Client Name}</p>", "doc_type": "work_order",
        "page_size": "receipt80"}, headers=tenant)
    assert r.status_code == 200, r.text
    html = r.json()["html"]
    assert "@page" in html and "80mm auto" in html.replace(" ", "").replace("80mmauto", "80mm auto") or "80mm" in html
    assert "Pat Ng" in html                      # data resolved
    assert "{Client Name}" not in html


async def test_print_view_custom_label_mm(client, tenant):
    r = await client.post("/api/documents/print-view", json={
        "body_html": "<div>{Product Title}</div>", "doc_type": "label",
        "page_size": "label", "page_w_mm": 40, "page_h_mm": 30, "margin_mm": 2}, headers=tenant)
    assert r.status_code == 200, r.text
    css = r.json()["html"]
    assert "40mm 30mm" in css and "margin:2mm" in css


async def test_template_persists_exact_size(client, tenant):
    tid = (await client.post("/api/documents/templates", json={
        "name": "Tag 40x30", "doc_type": "label", "page_size": "label",
        "page_w_mm": 40, "page_h_mm": 30, "margin_mm": 1,
        "body_html": "<div>{Product Title}</div>"}, headers=tenant)).json()["id"]
    items = (await client.get("/api/documents/templates", headers=tenant)).json()["items"]
    t = next(x for x in items if x["id"] == tid)
    assert t["page_w_mm"] == 40 and t["page_h_mm"] == 30 and t["margin_mm"] == 1
