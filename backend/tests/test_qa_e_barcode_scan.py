"""QA fix E5 — a barcode with a wrong check digit is saved with a warning, and an IMEI of a phone
in stock is found by /api/scan/resolve (the till's lookup already found it).
"""
import pytest

from backend.models import Part, SerializedItem


@pytest.mark.asyncio
async def test_bad_check_digit_barcode_is_saved_with_a_warning(client, tenant):
    r = await client.post("/api/catalog/products", json={"name": "Screen", "barcode": "5056178901235"},
                          headers=tenant)
    assert r.status_code == 201, r.text
    assert "check digit" in r.json()["warnings"][0] and "end in 1" in r.json()["warnings"][0]
    pid = r.json()["id"]
    good = await client.put(f"/api/catalog/products/{pid}", json={"name": "Screen", "barcode": "5056178901231"},
                            headers=tenant)
    assert good.status_code == 200 and "warnings" not in good.json(), good.text
    r = await client.post("/api/inventory/parts", json={"name": "Cable", "barcode": "4006381333932"},
                          headers=tenant)
    assert r.status_code == 201 and r.json()["warnings"], r.text
    # internal (non-GTIN-length) codes get no note
    r = await client.post("/api/catalog/products", json={"name": "Glue", "barcode": "12345"}, headers=tenant)
    assert "warnings" not in r.json()


@pytest.mark.asyncio
async def test_product_with_unknown_category_is_404(client, tenant):
    r = await client.post("/api/catalog/products", json={"name": "X", "category_id": "nope"}, headers=tenant)
    assert r.status_code == 404, r.text


@pytest.mark.asyncio
async def test_scan_resolve_finds_imei_of_phone_in_stock(client, tenant, db):
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    p = Part(org_id=me["org_id"], name="iPhone 13 128GB", serialized=True)
    db.add(p)
    await db.flush()
    si = SerializedItem(org_id=me["org_id"], part_id=p.id, serial_number="356789104512347", status="in_stock")
    db.add(si)
    await db.commit()
    r = await client.post("/api/scan/resolve", json={"code": "35-678910-451234-7"}, headers=tenant)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["matched"] and body["type"] == "product" and body["id"] == p.id
    assert body["serial_id"] == si.id and body["serial_status"] == "in_stock"
    # the same unit once sold isn't offered as stock
    si.status = "sold"
    await db.commit()
    r = await client.post("/api/scan/resolve", json={"code": "356789104512347"}, headers=tenant)
    assert r.json()["matched"] is False
