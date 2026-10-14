"""144ic — asset labels: render a template against assets, exact size, product×copies."""


async def test_asset_labels_render(client, tenant):
    tid = (await client.post("/api/documents/templates", json={
        "name": "Vessel tag", "doc_type": "vehicle_label", "page_size": "label",
        "page_w_mm": 50, "page_h_mm": 25, "margin_mm": 1,
        "body_html": "<div>{Asset Brand} {Asset Model} · {Asset Serial Number}</div><div>{Barcode}</div>"}, headers=tenant)).json()["id"]
    cid = (await client.post("/api/clients", json={"name": "Marina Co"}, headers=tenant)).json()["id"]
    aid = (await client.post("/api/assets", json={
        "client_id": cid, "brand": "Yamaha", "model": "242X", "serial": "HULL-77", "asset_type": "Vessel"}, headers=tenant)).json()["id"]
    r = await client.post("/api/documents/render-asset-labels", json={
        "template_id": tid, "asset_ids": [aid], "copies": 2}, headers=tenant)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 2
    html = body["html"]
    assert "50mm 25mm" in html and "page-break-after" in html
    assert "Yamaha" in html and "HULL-77" in html


async def test_asset_labels_need_ids(client, tenant):
    tid = (await client.post("/api/documents/templates", json={
        "name": "T", "doc_type": "vehicle_label", "body_html": "{Asset Brand}"}, headers=tenant)).json()["id"]
    r = await client.post("/api/documents/render-asset-labels", json={"template_id": tid, "asset_ids": []}, headers=tenant)
    assert r.status_code == 422
