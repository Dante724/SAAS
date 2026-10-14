"""144db — device-aware job kit: a product component flagged `device_variant` resolves, when the kit
is applied to a work order, to the in-category part that FITS that order's device (preferring one in
stock at the order's location). No device / no fit → the configured part is used."""
import pytest
from sqlalchemy import select


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


async def _part(client, db, h, loc, name, qty, category_id=None, fits=None):
    pid = (await client.post("/api/inventory/parts", json={"name": name, "sku": name.replace(" ", "")[:12], "sell_price": 100}, headers=h)).json()["id"]
    await client.post(f"/api/inventory/parts/{pid}/adjust", headers=h,
                      json={"location_id": loc, "qty": qty, "movement_type": "receive"})
    # category + compatibility are not on PartIn — set via ORM
    from backend.models import Part
    p = (await db.execute(select(Part).where(Part.id == pid))).scalar_one()
    if category_id:
        p.category_id = category_id
    if fits:
        p.compatibility_json = {"fits": fits}
    await db.commit()
    return pid


async def _category(client, h):
    return (await client.post("/api/catalog/categories", json={"name": "Screens", "entity_type": "product"}, headers=h)).json()["id"]


@pytest.mark.asyncio
async def test_variant_resolves_to_fitting_part_and_deducts_it(client, tenant, db):
    loc = await _loc(client, tenant)
    cat = await _category(client, tenant)
    generic = await _part(client, db, tenant, loc, "Generic Screen", 10, category_id=cat)             # no fits
    ip13 = await _part(client, db, tenant, loc, "iPhone 13 Screen", 10, category_id=cat,
                       fits=[{"brand": "Apple", "model": "iPhone 13"}])
    # kit: one device-variant product slot (default = generic)
    bid = (await client.post("/api/bundles", json={
        "name": "Screen combo", "price_mode": "sum", "price": 0,
        "components": [{"ref_type": "product", "ref_id": generic, "qty": 1, "device_variant": True}]},
        headers=tenant)).json()["id"]
    # a WO whose device is an iPhone 13
    cid = (await client.post("/api/clients", json={"name": "C"}, headers=tenant)).json()["id"]
    oid = (await client.post("/api/orders", json={
        "client_id": cid, "location_id": loc, "complaint": "x",
        "asset_brand": "Apple", "asset_model": "iPhone 13", "asset_serial": "SN-13"}, headers=tenant)).json()["id"]

    g0 = (await client.get(f"/api/inventory/parts/{generic}", headers=tenant)).json()["qty_available"]
    i0 = (await client.get(f"/api/inventory/parts/{ip13}", headers=tenant)).json()["qty_available"]
    r = await client.post(f"/api/orders/{oid}/lines", json={
        "kind": "bundle", "line_type": "part", "description": "Screen combo", "ref_id": bid, "qty": 1}, headers=tenant)
    assert r.status_code in (200, 201), r.text

    od = (await client.get(f"/api/orders/{oid}", headers=tenant)).json()
    refs = [ln.get("ref_id") for ln in od["lines"]]
    assert ip13 in refs, f"should have resolved to the iPhone 13 screen, line refs={refs}"
    assert generic not in refs, "the generic fallback should NOT be on the order"

    g1 = (await client.get(f"/api/inventory/parts/{generic}", headers=tenant)).json()["qty_available"]
    i1 = (await client.get(f"/api/inventory/parts/{ip13}", headers=tenant)).json()["qty_available"]
    assert g0 == g1, "generic stock must be untouched"
    assert i0 - i1 == 1, "the fitting part's stock must be deducted"


@pytest.mark.asyncio
async def test_variant_falls_back_when_no_device(client, tenant, db):
    loc = await _loc(client, tenant)
    cat = await _category(client, tenant)
    generic = await _part(client, db, tenant, loc, "Generic Screen", 10, category_id=cat)
    await _part(client, db, tenant, loc, "iPhone 13 Screen", 10, category_id=cat,
                fits=[{"brand": "Apple", "model": "iPhone 13"}])
    bid = (await client.post("/api/bundles", json={
        "name": "Screen combo", "price_mode": "sum", "price": 0,
        "components": [{"ref_type": "product", "ref_id": generic, "qty": 1, "device_variant": True}]},
        headers=tenant)).json()["id"]
    cid = (await client.post("/api/clients", json={"name": "C2"}, headers=tenant)).json()["id"]
    oid = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x"},
                              headers=tenant)).json()["id"]  # no device
    r = await client.post(f"/api/orders/{oid}/lines", json={
        "kind": "bundle", "line_type": "part", "description": "Screen combo", "ref_id": bid, "qty": 1}, headers=tenant)
    assert r.status_code in (200, 201), r.text
    od = (await client.get(f"/api/orders/{oid}", headers=tenant)).json()
    assert generic in [ln.get("ref_id") for ln in od["lines"]], "no device → configured part is used"
