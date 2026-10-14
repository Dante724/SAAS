"""144hk — branding, interactive CTAs, work summary, legal-entity variables."""


async def test_new_vars_in_catalog(client, tenant):
    d = (await client.get("/api/documents/templates", headers=tenant)).json()
    flat = set(d["variables"])
    for v in ("{Company Logo}", "{Brand Color}", "{Approve Button}", "{Pay Now Button}",
              "{Sign Here}", "{Verify Badge}", "{Work Summary}",
              "{Legal Entity VAT}", "{Legal Entity Bank Account}", "{Legal Entity Director}",
              "{Client First Name}", "{Client Last Name}", "{Client Tax ID}", "{Client Billing Address}",
              "{Order Urgent}", "{Order Scheduled Date}", "{Ad Campaign}", "{Location}", "{Location Address}",
              "{Total Amount Spelled Out}", "{Balance Due Spelled Out}", "{Order Payment URL}",
              "{Product Barcode}"):
        assert v in flat, v
    groups = {g["group"] for g in d["variable_groups"]}
    assert "Interactive" in groups


async def test_client_and_order_vars_render(client, tenant):
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    cid = (await client.post("/api/clients", json={
        "name": "Jane Roe", "source": "Google Ads", "tax_id": "TX-7",
        "billing_address": "1 Bill St"}, headers=tenant)).json()["id"]
    oid = (await client.post("/api/orders", json={
        "client_id": cid, "location_id": loc, "complaint": "x", "priority": 1}, headers=tenant)).json()["id"]
    tpl = (await client.post("/api/documents/templates", json={
        "name": "CV", "doc_type": "work_order",
        "body_html": "FN {Client First Name} LN {Client Last Name} · TAX {Client Tax ID} · "
                     "BILL {Client Billing Address} · AD {Ad Campaign} · U={Order Urgent}"},
        headers=tenant)).json()["id"]
    html = (await client.get(f"/api/documents/render/{oid}/{tpl}", headers=tenant)).text
    assert "FN Jane" in html and "LN Roe" in html
    assert "TX-7" in html and "1 Bill St" in html
    assert "Google Ads" in html and "U=Urgent" in html


async def test_interactive_and_summary_render(client, tenant):
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    cid = (await client.post("/api/clients", json={"name": "Rex"}, headers=tenant)).json()["id"]
    oid = (await client.post("/api/orders", json={
        "client_id": cid, "location_id": loc, "complaint": "Screen cracked"}, headers=tenant)).json()["id"]
    await client.post(f"/api/orders/{oid}/lines", json={
        "line_type": "labour", "kind": "service", "description": "Screen replacement",
        "unit_price": 90, "qty": 1}, headers=tenant)
    tpl = (await client.post("/api/documents/templates", json={
        "name": "Live", "doc_type": "estimate",
        "body_html": "<div>{Approve Button}</div><div>SUM {Work Summary}</div>"
                     "<div>{Sign Here}</div><div>{Verify Badge}</div>"}, headers=tenant)).json()["id"]
    html = (await client.get(f"/api/documents/render/{oid}/{tpl}", headers=tenant)).text
    assert "Approve online" in html                 # CTA rendered
    assert "Reported: Screen cracked." in html      # work summary composed from real fields
    assert "Work performed: Screen replacement." in html
    assert "Signature / Date" in html               # print-safe signature line
    assert "verify this" in html.lower()            # verify badge
    assert "{Approve Button}" not in html           # all substituted


async def test_legal_entity_fields_render(client, tenant):
    e = (await client.post("/api/settings/legal-entities", json={
        "name": "Acme LLC", "vat_no": "VAT-99", "bank_name": "First Bank",
        "director": "Jane Doe", "is_default": True}, headers=tenant))
    assert e.status_code == 201, e.text
    assert e.json()["vat_no"] == "VAT-99"
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    cid = (await client.post("/api/clients", json={"name": "Rex"}, headers=tenant)).json()["id"]
    oid = (await client.post("/api/orders", json={
        "client_id": cid, "location_id": loc, "complaint": "x"}, headers=tenant)).json()["id"]
    tpl = (await client.post("/api/documents/templates", json={
        "name": "LE", "doc_type": "invoice",
        "body_html": "VAT {Legal Entity VAT} · Dir {Legal Entity Director}"}, headers=tenant)).json()["id"]
    html = (await client.get(f"/api/documents/render/{oid}/{tpl}", headers=tenant)).text
    assert "VAT-99" in html and "Jane Doe" in html
