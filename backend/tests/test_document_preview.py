"""144hp — live preview endpoint renders an unsaved body against a real sample order."""


async def test_preview_renders_with_sample_order(client, tenant):
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    cid = (await client.post("/api/clients", json={"name": "Pat Lee"}, headers=tenant)).json()["id"]
    _oid = (await client.post("/api/orders", json={
        "client_id": cid, "location_id": loc, "complaint": "Broken hinge"}, headers=tenant)).json()["id"]
    r = await client.post("/api/documents/preview", json={
        "body_html": "<h1>{Company Name}</h1><p>For {Client Name} · {Order Malfunction}</p>",
        "doc_type": "work_order"}, headers=tenant)
    assert r.status_code == 200, r.text
    html = r.json()["html"]
    assert "Pat Lee" in html and "Broken hinge" in html
    assert "{Client Name}" not in html          # tokens resolved
    assert r.json()["sample"]                    # reported which order was used


async def test_preview_empty_org_does_not_error(client, tenant):
    # no orders needed: body with tokens still returns 200 (placeholders unresolved)
    r = await client.post("/api/documents/preview", json={
        "body_html": "<p>Hello {Client Name}</p>", "doc_type": "estimate"}, headers=tenant)
    assert r.status_code == 200, r.text
    assert "Hello" in r.json()["html"]
