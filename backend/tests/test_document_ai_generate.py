"""144ht — Tekly's own document builder: offline presets + describe. No 3rd-party API."""


async def test_presets_listed_and_build(client, tenant):
    d = (await client.get("/api/documents/smart-presets", headers=tenant)).json()
    keys = {p["key"] for p in d["items"]}
    assert "estimate_standard" in keys and "invoice_detailed" in keys
    r = await client.post("/api/documents/ai-generate", json={"preset": "estimate_standard"}, headers=tenant)
    assert r.status_code == 200, r.text
    assert r.json()["provider"] == "builtin"          # in-house, never a third party
    html = r.json()["body_html"]
    assert 'data-block="header"' in html and 'data-block="signature"' in html


async def test_ai_generate_builds_blocks(client, tenant):
    r = await client.post("/api/documents/ai-generate", json={
        "prompt": "branded estimate with logo, parts table, deposit line and a signature box",
        "doc_type": "estimate"}, headers=tenant)
    assert r.status_code == 200, r.text
    html = r.json()["body_html"]
    # keyword-mapped blocks are present
    assert 'data-block="header"' in html
    assert 'data-block="items"' in html
    assert 'data-block="deposit"' in html
    assert 'data-block="signature"' in html
    # tokens survive for the editor to pillify + the renderer to resolve
    assert "{Company Name}" in html and "{Product Name}" in html


async def test_ai_generate_requires_prompt(client, tenant):
    r = await client.post("/api/documents/ai-generate", json={"prompt": "  ", "doc_type": "estimate"}, headers=tenant)
    assert r.status_code == 400


async def test_ai_generated_body_renders(client, tenant):
    body = (await client.post("/api/documents/ai-generate", json={
        "prompt": "estimate with client, items and totals", "doc_type": "estimate"}, headers=tenant)).json()["body_html"]
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    cid = (await client.post("/api/clients", json={"name": "Neo Ng"}, headers=tenant)).json()["id"]
    await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x"}, headers=tenant)
    r = await client.post("/api/documents/preview", json={"body_html": body, "doc_type": "estimate"}, headers=tenant)
    assert r.status_code == 200, r.text
    assert "Neo Ng" in r.json()["html"]                 # {Client Name} resolved through the engine
    assert "{Client Name}" not in r.json()["html"]
