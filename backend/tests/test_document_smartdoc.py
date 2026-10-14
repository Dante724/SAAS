"""144id — smart-doc: conditional blocks + reusable components resolve at render time."""


async def test_conditional_shows_and_hides(client, tenant):
    body = ("Base always.{#if Invoice Comment} note: {Invoice Comment}{/if}"
            "{#unless Invoice Comment} (no note){/unless}")
    _tid = (await client.post("/api/documents/templates", json={
        "name": "Cond", "doc_type": "invoice", "body_html": body}, headers=tenant)).json()["id"]
    # No invoices yet → {Invoice Comment} resolves to its placeholder / empty, so the
    # if-branch drops and the unless-branch stays.
    r = await client.post("/api/documents/preview", json={
        "body_html": body, "doc_type": "invoice"}, headers=tenant)
    assert r.status_code == 200, r.text
    html = r.json()["html"]
    assert "Base always." in html
    assert "note:" not in html
    assert "(no note)" in html


async def test_conditional_else_branch(client, tenant):
    body = "{#if Client Name}Hi {Client Name}{#else}Valued customer{/if}"
    r = await client.post("/api/documents/preview", json={
        "body_html": body, "doc_type": "invoice"}, headers=tenant)
    assert r.status_code == 200, r.text
    html = r.json()["html"]
    # empty org → no client → else branch
    assert "Valued customer" in html and "Hi " not in html


async def test_component_inlined(client, tenant):
    # a reusable header component
    await client.post("/api/documents/templates", json={
        "name": "Brand Header", "doc_type": "component",
        "body_html": "<div class='hdr'>ACME REPAIRS</div>"}, headers=tenant)
    body = "{{component: Brand Header}}<p>Body</p>"
    r = await client.post("/api/documents/preview", json={
        "body_html": body, "doc_type": "invoice"}, headers=tenant)
    assert r.status_code == 200, r.text
    html = r.json()["html"]
    assert "ACME REPAIRS" in html and "Body" in html
    assert "{{component" not in html


async def test_component_not_in_print_dropdown(client, tenant):
    await client.post("/api/documents/templates", json={
        "name": "Footer", "doc_type": "component", "body_html": "x"}, headers=tenant)
    oid = (await client.post("/api/orders", json={"title": "Fix"}, headers=tenant)).json()["id"]
    r = await client.get(f"/api/documents/templates?order_id={oid}", headers=tenant)
    assert r.status_code == 200, r.text
    names = [i["name"] for i in r.json()["items"]]
    assert "Footer" not in names
