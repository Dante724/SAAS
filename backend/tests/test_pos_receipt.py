"""F9 (launch QA 2026-10-19): after a POS sale the seller can print, email or WhatsApp the
customer a receipt, and the receipt never shows cost or profit."""
import pytest


async def _sale(client, h, client_id=None):
    r = await client.post("/api/sales", json={"client_id": client_id, "lines": [
        {"kind": "service", "ref_id": None, "description": "Screen clean", "qty": 2, "unit_price": 150}]}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


@pytest.mark.asyncio
async def test_receipt_lists_items_and_total_but_not_profit(client, tenant):
    cid = (await client.post("/api/clients", json={"name": "Ana", "email": "ana@example.com", "phone": "+15550100"},
                             headers=tenant)).json()["id"]
    sale = await _sale(client, tenant, cid)
    rc = (await client.get(f"/api/sales/{sale['id']}/receipt", headers=tenant)).json()
    assert rc["sale_number"] == sale["sale_number"]
    assert rc["client_email"] == "ana@example.com" and rc["client_phone"] == "+15550100"
    assert "Screen clean" in rc["text"] and "2 × " in rc["text"] and "300" in rc["text"]
    assert "profit" not in rc["text"].lower() and "cost" not in rc["text"].lower()
    assert "profit" not in rc and "total_cost" not in rc


@pytest.mark.asyncio
async def test_email_receipt_to_client_or_typed_address(client, tenant):
    cid = (await client.post("/api/clients", json={"name": "Ana", "email": "ana@example.com"}, headers=tenant)).json()["id"]
    sale = await _sale(client, tenant, cid)
    r = await client.post(f"/api/sales/{sale['id']}/receipt/email", json={}, headers=tenant)
    assert r.status_code == 200, r.text
    assert r.json()["recipient"] == "ana@example.com"
    walk_in = await _sale(client, tenant)
    r = await client.post(f"/api/sales/{walk_in['id']}/receipt/email", json={}, headers=tenant)
    assert r.status_code == 422                                   # walk-in, no address given
    r = await client.post(f"/api/sales/{walk_in['id']}/receipt/email", json={"email": "bo@example.com"}, headers=tenant)
    assert r.status_code == 200 and r.json()["recipient"] == "bo@example.com"


@pytest.mark.asyncio
async def test_receipt_of_another_shop_is_404(client, tenant):
    r = await client.get("/api/sales/not-a-sale/receipt", headers=tenant)
    assert r.status_code == 404
