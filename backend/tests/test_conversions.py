"""Build 76/77 — Product ↔ Asset conversion.

Covers the spec's rules: serialized-only, no conversion from an open work order,
immutable conversion documents, inventory effects, dedupe search, reversal by
opposite conversion (never by deletion), and permission enforcement.
"""
import pytest
from sqlalchemy import select

from backend.models import AssetProductConversion, Device, Part, SerializedItem, StockLevel


async def _loc(client, H):
    return (await client.get("/api/staff/locations", headers=H)).json()["items"][0]["id"]


async def _me(client, H):
    return (await client.get("/api/auth/me", headers=H)).json()


async def _serialized_unit(client, tenant, db, serial="SN-1", serialized=True):
    """A product (optionally serialized) with one stock unit on hand."""
    me = await _me(client, tenant)
    loc = await _loc(client, tenant)
    part = Part(org_id=me["org_id"], name="Laptop X", sku="LX-1", brand="Acme",
                cost_price=400, sell_price=700, serialized=serialized)
    db.add(part)
    await db.flush()
    unit = SerializedItem(org_id=me["org_id"], part_id=part.id, serial_number=serial,
                          location_id=loc, unit_cost=400, status="in_stock")
    db.add(unit)
    db.add(StockLevel(part_id=part.id, location_id=loc, qty_available=1, qty_reserved=0))
    await db.commit()
    await db.refresh(part)
    await db.refresh(unit)
    return part, unit, loc


# ── Product → Asset ───────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_product_to_asset_creates_asset_and_document(client, tenant, db):
    part, unit, loc = await _serialized_unit(client, tenant, db)
    r = await client.post("/api/conversions/product-to-asset", json={
        "serialized_item_id": unit.id, "owner_type": "company", "condition": "new"}, headers=tenant)
    assert r.status_code == 201, r.text
    body = r.json()

    dev = (await db.execute(select(Device).where(Device.id == body["device_id"]))).scalar_one()
    assert dev.serial == "SN-1"                    # identifier carried across
    assert dev.brand == "Acme"
    assert dev.owner_type == "company"

    conv = body["conversion"]
    assert conv["direction"] == "product_to_asset"
    assert conv["serial_identifier"] == "SN-1"
    assert conv["owner_before"] == "company" and conv["status"] == "completed"

    await db.refresh(unit)
    assert unit.status == "converted_to_asset"     # unit linked to the asset
    assert unit.converted_asset_id == dev.id
    sl = (await db.execute(select(StockLevel).where(
        StockLevel.part_id == part.id, StockLevel.location_id == loc))).scalar_one()
    await db.refresh(sl)
    assert sl.qty_available == 0                   # left available inventory


@pytest.mark.asyncio
async def test_non_serialized_product_cannot_convert(client, tenant, db):
    _, unit, _ = await _serialized_unit(client, tenant, db, serial="SN-2", serialized=False)
    r = await client.post("/api/conversions/product-to-asset", json={
        "serialized_item_id": unit.id}, headers=tenant)
    assert r.status_code == 400
    assert "serial" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_unit_on_open_order_cannot_convert(client, tenant, db):
    part, unit, loc = await _serialized_unit(client, tenant, db, serial="SN-3")
    o = await client.post("/api/orders", json={"location_id": loc, "complaint": "x"}, headers=tenant)
    assert o.status_code == 201, o.text
    unit.order_id = o.json()["id"]
    unit.status = "allocated_to_order"
    await db.commit()

    r = await client.post("/api/conversions/product-to-asset", json={
        "serialized_item_id": unit.id}, headers=tenant)
    assert r.status_code == 409
    assert "open work order" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_double_conversion_blocked(client, tenant, db):
    _, unit, _ = await _serialized_unit(client, tenant, db, serial="SN-4")
    a = await client.post("/api/conversions/product-to-asset", json={
        "serialized_item_id": unit.id}, headers=tenant)
    assert a.status_code == 201
    b = await client.post("/api/conversions/product-to-asset", json={
        "serialized_item_id": unit.id}, headers=tenant)
    assert b.status_code == 409


@pytest.mark.asyncio
async def test_prefill_product_to_asset(client, tenant, db):
    part, unit, _ = await _serialized_unit(client, tenant, db, serial="SN-5")
    p = (await client.get(f"/api/conversions/prefill/product/{unit.id}", headers=tenant)).json()
    assert p["serial"] == "SN-5" and p["brand"] == "Acme"
    assert p["owner_type"] == "company" and p["part_id"] == part.id


# ── Asset → Product ───────────────────────────────────────────────────────────
async def _asset(client, tenant, db, brand="Sony", model="A7", serial="AS-1"):
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    c = (await client.post("/api/clients", json={"name": "Owner", "phone": "+199"},
                           headers=tenant)).json()
    d = Device(org_id=me["org_id"], client_id=c["id"], owner_type="client", brand=brand, model=model,
               serial=serial, purchase_price=300, sale_price=500)
    db.add(d)
    await db.commit()
    await db.refresh(d)
    return d


@pytest.mark.asyncio
async def test_asset_to_product_creates_product_with_metadata(client, tenant, db):
    d = await _asset(client, tenant, db)
    r = await client.post("/api/conversions/asset-to-product", json={
        "device_id": d.id, "name": "Sony A7 (used)", "cost_price": 300, "sell_price": 500},
        headers=tenant)
    assert r.status_code == 201, r.text
    pid = r.json()["part_id"]
    part = (await db.execute(select(Part).where(Part.id == pid))).scalar_one()
    assert part.serialized is True                  # it has a unique identity
    assert part.asset_brand == "Sony" and part.asset_model == "A7"
    assert part.sku is None                         # serial must NOT auto-become the SKU

    conv = r.json()["conversion"]
    assert conv["direction"] == "asset_to_product"
    assert conv["owner_before"] == "client" and conv["owner_after"] == "company"

    await db.refresh(d)
    assert d.converted_to_part_id == pid


@pytest.mark.asyncio
async def test_convert_adds_stock_quantity(client, tenant, db):
    """Build 121: converting an asset must add 1 to the product's available stock."""
    from sqlalchemy import func as _f

    from backend.models import StockLevel
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    d = Device(org_id=me["org_id"], owner_type="company", brand="Apple", model="iPad",
               serial="QTY-1", purchase_price=300, sale_price=500)
    db.add(d)
    await db.commit()
    await db.refresh(d)
    r = await client.post(f"/api/assets/{d.id}/convert-to-product", json={
        "sale_price": 200}, headers=tenant)
    assert r.status_code == 201, r.text
    pid = r.json().get("part_id") or r.json().get("product", {}).get("id")
    total = (await db.execute(select(_f.coalesce(_f.sum(StockLevel.qty_available), 0))
        .where(StockLevel.part_id == pid))).scalar()
    assert total == 1, f"expected 1 unit in stock after conversion, got {total}"


@pytest.mark.asyncio
async def test_asset_to_product_preserves_history(client, tenant, db):
    """Build 117: converting an asset snapshots its service history into the audit trail."""
    from backend.models import AssetProductConversion
    d = await _asset(client, tenant, db, serial="HIST-1")
    r = await client.post("/api/conversions/asset-to-product", json={
        "device_id": d.id, "name": "Used unit", "cost_price": 100, "sell_price": 200},
        headers=tenant)
    assert r.status_code == 201, r.text
    conv_id = r.json()["conversion"]["id"]
    conv = (await db.execute(select(AssetProductConversion).where(
        AssetProductConversion.id == conv_id))).scalar_one()
    hist = (conv.metadata_json or {}).get("history")
    assert hist is not None, "history snapshot missing"
    assert "work_orders" in hist and "work_order_count" in hist
    assert hist["serial"] == "HIST-1"
    # the device row is preserved (not deleted), so its history stays reachable
    await db.refresh(d)
    assert d.converted_to_part_id is not None


@pytest.mark.asyncio
async def test_asset_to_product_can_link_existing_product(client, tenant, db):
    """Dedupe: link an existing product instead of creating a duplicate."""
    me = await _me(client, tenant)
    existing = Part(org_id=me["org_id"], name="Sony A7", serialized=True)
    db.add(existing)
    await db.commit()
    await db.refresh(existing)

    d = await _asset(client, tenant, db, serial="AS-2")
    pre = (await client.get(f"/api/conversions/prefill/asset/{d.id}", headers=tenant)).json()
    assert any(m["id"] == existing.id for m in pre["possible_duplicates"])   # surfaced first

    r = await client.post("/api/conversions/asset-to-product", json={
        "device_id": d.id, "part_id": existing.id}, headers=tenant)
    assert r.status_code == 201, r.text
    assert r.json()["part_id"] == existing.id
    assert r.json()["conversion"]["metadata"]["linked_existing"] is True


@pytest.mark.asyncio
async def test_asset_to_product_optional_stock_unit(client, tenant, db):
    d = await _asset(client, tenant, db, serial="AS-3")
    loc = await _loc(client, tenant)
    r = await client.post("/api/conversions/asset-to-product", json={
        "device_id": d.id, "create_stock_unit": True, "location_id": loc, "cost_price": 250},
        headers=tenant)
    assert r.status_code == 201, r.text
    pid = r.json()["part_id"]
    unit = (await db.execute(select(SerializedItem).where(
        SerializedItem.part_id == pid))).scalar_one()
    assert unit.serial_number == "AS-3" and unit.status == "in_stock"
    sl = (await db.execute(select(StockLevel).where(
        StockLevel.part_id == pid, StockLevel.location_id == loc))).scalar_one()
    assert sl.qty_available == 1                    # became company inventory


# ── Immutability & reversal ───────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_conversion_document_cannot_be_deleted(client, tenant, db):
    _, unit, _ = await _serialized_unit(client, tenant, db, serial="SN-6")
    r = await client.post("/api/conversions/product-to-asset", json={
        "serialized_item_id": unit.id}, headers=tenant)
    cid = r.json()["conversion"]["id"]
    d = await client.delete(f"/api/conversions/{cid}", headers=tenant)
    assert d.status_code == 405                     # immutable by design
    assert "convert the item back" in d.json()["detail"].lower()


@pytest.mark.asyncio
async def test_reversal_is_opposite_conversion_not_deletion(client, tenant, db):
    """Converting back the other way marks the first document reversed and links
    the pair — both documents survive."""
    _, unit, _ = await _serialized_unit(client, tenant, db, serial="SN-7")
    a = await client.post("/api/conversions/product-to-asset", json={
        "serialized_item_id": unit.id}, headers=tenant)
    conv_a = a.json()["conversion"]["id"]
    dev_id = a.json()["device_id"]

    b = await client.post("/api/conversions/asset-to-product", json={
        "device_id": dev_id, "name": "Back to product"}, headers=tenant)
    assert b.status_code == 201, b.text
    conv_b = b.json()["conversion"]["id"]

    rows = {c.id: c for c in (await db.execute(
        select(AssetProductConversion))).scalars().all()}
    assert len(rows) == 2                                   # both kept
    await db.refresh(rows[conv_a])
    assert rows[conv_a].status == "reversed"
    assert rows[conv_a].reversed_by_id == conv_b
    assert rows[conv_b].metadata_json.get("reverses") == conv_a


@pytest.mark.asyncio
async def test_conversions_list_filters_by_direction(client, tenant, db):
    _, unit, _ = await _serialized_unit(client, tenant, db, serial="SN-8")
    await client.post("/api/conversions/product-to-asset", json={
        "serialized_item_id": unit.id}, headers=tenant)
    lst = (await client.get("/api/conversions?direction=product_to_asset", headers=tenant)).json()
    assert lst["items"] and all(i["direction"] == "product_to_asset" for i in lst["items"])
    assert lst["items"][0]["employee_name"]              # decorated for the UI
    none = (await client.get("/api/conversions?direction=asset_to_product", headers=tenant)).json()
    assert none["items"] == []


@pytest.mark.asyncio
async def test_conversion_permission_enforced(client, tenant, db):
    """A role that denies the conversion permission is blocked."""
    from backend.models import Employee, Role
    from backend.services.auth import hash_password
    me = await _me(client, tenant)
    role = Role(org_id=me["org_id"], name="No convert",
                permissions={"can_convert_product_to_asset": False})
    db.add(role)
    await db.flush()
    emp = Employee(org_id=me["org_id"], name="Junior", email="junior-conv@x.dev",
                   password_hash=hash_password("pw-123456789"), active=True, role_id=role.id)
    db.add(emp)
    await db.commit()

    tok = (await client.post("/api/auth/token", data={
        "username": "junior-conv@x.dev", "password": "pw-123456789"})).json()["access_token"]
    H = {"Authorization": f"Bearer {tok}"}
    _, unit, _ = await _serialized_unit(client, tenant, db, serial="SN-9")
    r = await client.post("/api/conversions/product-to-asset", json={
        "serialized_item_id": unit.id}, headers=H)
    assert r.status_code == 403
