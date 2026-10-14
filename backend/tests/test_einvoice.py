"""144if — in-house e-invoicing: issue UBL 2.1, set status, download (staff + public track)."""


async def _order_invoice(client, tenant):
    oid = (await client.post("/api/orders", json={"complaint": "Screen"}, headers=tenant)).json()["id"]
    iid = (await client.post("/api/finance/invoices", json={"order_id": oid, "subtotal": 120}, headers=tenant)).json()["id"]
    return oid, iid


async def test_issue_generates_ubl_and_number(client, tenant):
    _, iid = await _order_invoice(client, tenant)
    r = await client.post(f"/api/finance/invoices/{iid}/einvoice", headers=tenant)
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["einvoice_status"] == "issued"
    assert b["einvoice_number"].startswith("EINV-")
    assert b["einvoice_format"] == "ubl2.1"
    # download the XML
    x = await client.get(f"/api/finance/invoices/{iid}/einvoice.xml", headers=tenant)
    assert x.status_code == 200, x.text
    xml = x.text
    assert "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2" in xml
    assert "<cbc:InvoiceTypeCode>380</cbc:InvoiceTypeCode>" in xml
    assert "PayableAmount" in xml


async def test_status_flow(client, tenant):
    _, iid = await _order_invoice(client, tenant)
    # cannot set status before issuing
    pre = await client.patch(f"/api/finance/invoices/{iid}/einvoice", json={"status": "cleared"}, headers=tenant)
    assert pre.status_code == 409
    await client.post(f"/api/finance/invoices/{iid}/einvoice", headers=tenant)
    ok = await client.patch(f"/api/finance/invoices/{iid}/einvoice", json={"status": "cleared"}, headers=tenant)
    assert ok.status_code == 200 and ok.json()["einvoice_status"] == "cleared"
    bad = await client.patch(f"/api/finance/invoices/{iid}/einvoice", json={"status": "wat"}, headers=tenant)
    assert bad.status_code == 422


async def test_download_before_issue_404(client, tenant):
    _, iid = await _order_invoice(client, tenant)
    r = await client.get(f"/api/finance/invoices/{iid}/einvoice.xml", headers=tenant)
    assert r.status_code == 404


async def test_public_track_exposes_einvoice(client, tenant):
    oid, iid = await _order_invoice(client, tenant)
    await client.post(f"/api/finance/invoices/{iid}/einvoice", headers=tenant)
    # mint the order's public track link
    token = (await client.post(f"/api/orders/{oid}/public-link", headers=tenant)).json()["token"]
    assert token, "order should have a public token"
    t = await client.get(f"/api/public/track/{token}")
    assert t.status_code == 200, t.text
    inv = t.json().get("invoice")
    assert inv and inv["einvoice_available"] is True
    # public token-gated XML download (no auth header)
    x = await client.get(f"/api/public/track/{token}/einvoice.xml")
    assert x.status_code == 200 and "Invoice-2" in x.text
