"""Build 144an — POS findability: products resolve by SKU, barcode, and serial number,
and the list search matches SKU/barcode too."""
import pytest

from backend.models import Part, SerializedItem


async def _org(client, tenant):
    return (await client.get("/api/auth/me", headers=tenant)).json()["org_id"]


@pytest.mark.asyncio
async def test_search_matches_sku_and_barcode(db, client, tenant):
    org_id = await _org(client, tenant)
    db.add(Part(org_id=org_id, name="Blue Widget", sku="BW-77", barcode="8901234567", sell_price=10))
    await db.commit()
    # by SKU fragment
    r = (await client.get("/api/inventory/parts?search=BW-77", headers=tenant)).json()
    assert any(i["sku"] == "BW-77" for i in r["items"])
    # by barcode fragment
    r = (await client.get("/api/inventory/parts?search=890123", headers=tenant)).json()
    assert any(i["name"] == "Blue Widget" for i in r["items"])


@pytest.mark.asyncio
async def test_scan_resolves_sku_barcode_serial(db, client, tenant):
    org_id = await _org(client, tenant)
    p = Part(org_id=org_id, name="Cable", sku="CB-1", barcode="555000111", code="INT-9", sell_price=5)
    db.add(p)
    await db.flush()
    db.add(SerializedItem(org_id=org_id, part_id=p.id, serial_number="SN-ABC-123", status="in_stock"))
    await db.commit()

    # SKU
    r = (await client.get("/api/inventory/scan?code=CB-1", headers=tenant)).json()
    assert r["matched_on"] == "sku" and r["product"]["id"] == p.id
    # barcode
    r = (await client.get("/api/inventory/scan?code=555000111", headers=tenant)).json()
    assert r["matched_on"] == "barcode"
    # internal code
    r = (await client.get("/api/inventory/scan?code=INT-9", headers=tenant)).json()
    assert r["matched_on"] == "code"
    # serial number → resolves to its product
    r = (await client.get("/api/inventory/scan?code=SN-ABC-123", headers=tenant)).json()
    assert r["matched_on"] == "serial" and r["serial"] == "SN-ABC-123" and r["product"]["id"] == p.id
    # unknown code → 404
    assert (await client.get("/api/inventory/scan?code=NOPE", headers=tenant)).status_code == 404


@pytest.mark.asyncio
async def test_scan_is_org_scoped(db, client, tenant):
    # a serial in another org must not resolve for this tenant. The other org must really exist
    # now that FKs are enforced (parts.org_id → organisations); a fabricated id would fail on insert.
    from backend.models import Organisation
    other = "11111111-1111-1111-1111-111111111111"
    db.add(Organisation(id=other, name="Other Org"))
    await db.flush()
    p = Part(org_id=other, name="Foreign", sku="FX-1", sell_price=1)
    db.add(p)
    await db.flush()
    db.add(SerializedItem(org_id=other, part_id=p.id, serial_number="FS-1", status="in_stock"))
    await db.commit()
    assert (await client.get("/api/inventory/scan?code=FX-1", headers=tenant)).status_code == 404
    assert (await client.get("/api/inventory/scan?code=FS-1", headers=tenant)).status_code == 404
