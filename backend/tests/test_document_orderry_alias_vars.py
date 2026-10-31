"""144ho — Orderry-exact variable names (aliases + new data-backed vars) and
org custom fields auto-exposed as {…} document variables."""


async def test_orderry_exact_names_in_catalog(client, tenant):
    d = (await client.get("/api/documents/templates", headers=tenant)).json()
    flat = set(d["variables"])
    for v in ("{Company Details}", "{Company Note}",
              "{Legal Entity Registration Number}", "{Legal Entity TIN}", "{Legal Entity IBAN}",
              "{Client Tax Identification Number}", "{Client Business Registration Number}",
              "{Payer First Name}", "{Payer Tax Identification Number}",
              "{Order Malfunction}", "{Order Comment}", "{Order Address}", "{Order Duration}",
              "{Order Scheduled For}", "{Order Type}", "{Urgent}",
              "{Estimated Price}", "{Order sum}", "{Balance Due}", "{Total Paid Amount}",
              "{Total Amount Without Taxes}",
              "{Today Datetime}", "{Order Closed Date}", "{Order Due Date Time}",
              "{Public Page URL}", "{Order URL}", "{Public Page QR Code}",
              "{Client Feedback QR Code}", "{Order Payment QR}"):
        assert v in flat, v


async def test_aliases_resolve_to_same_values(client, tenant):
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    cid = (await client.post("/api/clients", json={
        "name": "Ada Byron", "tax_id": "TX-42", "reg_no": "RG-9"}, headers=tenant)).json()["id"]
    oid = (await client.post("/api/orders", json={
        "client_id": cid, "location_id": loc, "complaint": "No power", "priority": 1}, headers=tenant)).json()["id"]
    body = ("TOTAL {Order Total}|SUM {Order sum}|"
            "TAX {Client Tax ID}|TIN {Client Tax Identification Number}|"
            "MAL {Order Malfunction}|CMP {Order Complaint}|"
            "U1 {Order Urgent}|U2 {Urgent}|"
            "PAGE {Public Page URL}|TRK {Tracking URL}|"
            "DET [{Company Details}]")
    tpl = (await client.post("/api/documents/templates", json={
        "name": "Alias", "doc_type": "work_order", "body_html": body}, headers=tenant)).json()["id"]
    html = (await client.get(f"/api/documents/render/{oid}/{tpl}", headers=tenant)).text
    # each Orderry-exact name renders the same value as its Tekly counterpart
    import re
    def val(label):
        return re.search(rf"{label} (.*?)\|", html + "|").group(1)
    assert val("TOTAL") == val("SUM")
    assert val("TAX") == val("TIN") == "TX-42"
    assert val("MAL") == val("CMP") == "No power"
    assert val("U1") == val("U2")            # both "Urgent" (priority 1)
    assert val("PAGE") == val("TRK")         # public-page alias == tracking URL
    assert "{Order sum}" not in html and "{Urgent}" not in html   # all substituted


async def test_custom_field_becomes_variable(client, tenant):
    # define a client custom field, then it must appear in the catalog and render
    f = (await client.post("/api/forms/entity-fields", json={
        "entity_type": "client", "field_label": "VIP Level", "field_type": "text"},
        headers=tenant)).json()
    key = f["field_key"]

    d = (await client.get("/api/documents/templates", headers=tenant)).json()
    assert "Custom fields" in {g["group"] for g in d["variable_groups"]}
    assert "{Client VIP Level}" in set(d["variables"])

    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    cid = (await client.post("/api/clients", json={
        "name": "Grace H", "custom_data": {key: "Gold"}}, headers=tenant)).json()["id"]
    oid = (await client.post("/api/orders", json={
        "client_id": cid, "location_id": loc, "complaint": "x"}, headers=tenant)).json()["id"]
    tpl = (await client.post("/api/documents/templates", json={
        "name": "CF", "doc_type": "work_order",
        "body_html": "VIP={Client VIP Level}"}, headers=tenant)).json()["id"]
    html = (await client.get(f"/api/documents/render/{oid}/{tpl}", headers=tenant)).text
    assert "VIP=Gold" in html
