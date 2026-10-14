"""Build 144n — document template layout (page size/font) + grouped variables."""
import pytest


@pytest.mark.asyncio
async def test_grouped_variables_and_new_vars(client, tenant):
    d = (await client.get("/api/documents/templates", headers=tenant)).json()
    assert "variable_groups" in d
    groups = {g["group"]: g["items"] for g in d["variable_groups"]}
    assert "Company" in groups and "Client" in groups and "Finance" in groups
    # new variables are advertised
    flat = d["variables"]
    for v in ("{Company Phone}", "{Company Email}", "{Company Website}",
              "{Order Due Date}", "{Current Date}", "{Currency}", "{Asset IMEI}"):
        assert v in flat, v


@pytest.mark.asyncio
async def test_template_stores_page_size_and_font(client, tenant):
    r = await client.post("/api/documents/templates", json={
        "name": "Estimate", "doc_type": "estimate",
        "body_html": "<h1>ESTIMATE #{Order Number}</h1>",
        "page_size": "letter", "font_family": "Serif"}, headers=tenant)
    assert r.status_code == 201, r.text
    tid = r.json()["id"]

    row = next(t for t in (await client.get("/api/documents/templates", headers=tenant)).json()["items"]
               if t["id"] == tid)
    assert row["page_size"] == "letter"
    assert row["font_family"] == "Serif"

    # edit page size
    upd = await client.put(f"/api/documents/templates/{tid}", json={
        "name": "Estimate", "doc_type": "estimate", "body_html": "x", "page_size": "a4"},
        headers=tenant)
    assert upd.status_code == 200
    row2 = next(t for t in (await client.get("/api/documents/templates", headers=tenant)).json()["items"]
                if t["id"] == tid)
    assert row2["page_size"] == "a4"


@pytest.mark.asyncio
async def test_new_variables_render_from_order(client, tenant):
    # set some company info so the new company variables resolve to real values
    await client.put("/api/staff/company", json={
        "company_phone": "+1-555-0100", "company_email": "shop@example.com",
        "website": "https://shop.example"}, headers=tenant)
    tpl = (await client.post("/api/documents/templates", json={
        "name": "Card", "doc_type": "work_order",
        "body_html": "Phone {Company Phone} / Web {Company Website} / Cur {Currency}"},
        headers=tenant)).json()["id"]

    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    cid = (await client.post("/api/clients", json={"name": "Rex", "phone": "+15550009"},
                             headers=tenant)).json()["id"]
    oid = (await client.post("/api/orders", json={
        "client_id": cid, "location_id": loc, "complaint": "x"}, headers=tenant)).json()["id"]

    html = (await client.get(f"/api/documents/render/{oid}/{tpl}", headers=tenant)).text
    assert "+1-555-0100" in html
    assert "https://shop.example" in html
    assert "{Company Phone}" not in html   # variable was substituted
