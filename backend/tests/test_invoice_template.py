"""144hn — invoices render through the customizable document template engine."""


async def test_invoice_vars_in_catalog(client, tenant):
    d = (await client.get("/api/documents/templates", headers=tenant)).json()
    flat = set(d["variables"])
    for v in ("{Invoice Number}", "{Invoice Date}", "{Invoice Total}", "{Invoice Table}",
              "{Invoice Balance Due}", "{Invoice Payment URL}"):
        assert v in flat, v
    assert "Invoice" in {g["group"] for g in d["variable_groups"]}


async def test_invoice_renders_through_template(client, tenant):
    cid = (await client.post("/api/clients", json={"name": "Zed Corp", "tax_id": "TX-1"}, headers=tenant)).json()["id"]
    inv = (await client.post("/api/finance/invoices", json={
        "client_id": cid, "comment": "Thanks!",
        "lines": [{"kind": "custom", "description": "Diagnostics", "qty": 1, "unit_price": 40},
                  {"kind": "custom", "description": "Part X", "qty": 2, "unit_price": 15}]},
        headers=tenant)).json()
    iid = inv["id"]
    tpl = (await client.post("/api/documents/templates", json={
        "name": "Invoice A5", "doc_type": "invoice",
        "body_html": "<h1>INVOICE {Invoice Number}</h1><p>{Client Name} · {Client Tax ID}</p>"
                     "<table><tbody><tr><td>{Row Number}</td><td>{Product Name}</td>"
                     "<td>{Product Amount}</td></tr></tbody></table>"
                     "<p>Total {Invoice Total} · Due {Invoice Balance Due} · {Invoice Comment}</p>"},
        headers=tenant)).json()["id"]
    html = (await client.get(f"/api/documents/render-invoice/{iid}/{tpl}", headers=tenant)).text
    assert "Diagnostics" in html and "Part X" in html      # rows expanded
    assert "Zed Corp" in html and "TX-1" in html
    assert "Thanks!" in html
    assert "{Product Name}" not in html and "{Invoice Total}" not in html   # all substituted
