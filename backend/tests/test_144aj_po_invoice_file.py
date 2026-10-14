"""144aj — attach the supplier's invoice PDF to a PO (evidence for three-way match)."""
import base64

import pytest


@pytest.mark.asyncio
async def test_attach_and_fetch_supplier_invoice(client, tenant):
    sid = (await client.post("/api/suppliers", json={"name": "Acme"}, headers=tenant)).json()["id"]
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    pid = (await client.post("/api/inventory/parts", json={"name": "Belt", "sku": "B-1"}, headers=tenant)).json()["id"]
    po_id = (await client.post("/api/inventory/po", headers=tenant, json={
        "supplier_id": sid, "location_id": loc, "lines": [{"part_id": pid, "qty_ordered": 1, "unit_cost": 5}]})).json()["po_id"]

    pdf = "data:application/pdf;base64," + base64.b64encode(b"%PDF-1.4 fake invoice").decode()
    r = await client.post(f"/api/inventory/po/{po_id}/invoice", headers=tenant,
                          json={"supplier_invoice_total": 5, "invoice_file": pdf, "invoice_name": "acme-inv-99.pdf"})
    assert r.status_code == 200, r.text

    detail = (await client.get(f"/api/inventory/po/{po_id}", headers=tenant)).json()
    f = detail["supplier_invoice_file"]
    assert f and f["name"] == "acme-inv-99.pdf" and f["url"].startswith("/api/media/")

    # the stored file streams back
    got = await client.get(f["url"], headers=tenant)
    assert got.status_code == 200 and got.content.startswith(b"%PDF")
