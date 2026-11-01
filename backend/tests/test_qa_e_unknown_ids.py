"""QA fix E1 — unknown or another shop's ids give a 404/422, never a 500 or a silent save.

Found in the 5-shop QA run (31 Oct 2026): warehouse create, stock adjust and PO create with a
warehouse id where a location id belongs, tax rules with an unknown tax, and the products bulk
"category" action with an unknown category all failed on the foreign key with a 500. Another
shop's tax id was stored in the tax-rules stack and charged as 0%.
"""
import pytest

from backend.models import Organisation, Tax


async def _ctx(client, h):
    loc = (await client.get("/api/staff/locations", headers=h)).json()
    loc = loc["items"][0] if isinstance(loc, dict) else loc[0]
    whs = (await client.get("/api/settings/warehouses", headers=h)).json()["items"]
    part = (await client.post("/api/inventory/parts", json={"name": "Screen", "sku": "SCR-1"},
                              headers=h)).json()
    return loc["id"], whs, part["id"]


async def _foreign_tax(db):
    other = Organisation(name="Other shop")
    db.add(other)
    await db.flush()
    t = Tax(org_id=other.id, name="Their VAT", rate=20)
    db.add(t)
    await db.commit()
    return t.id


@pytest.mark.asyncio
async def test_warehouse_create_with_unknown_location_is_404(client, tenant):
    r = await client.post("/api/settings/warehouses", json={
        "name": "Back room", "location_id": "no-such-location"}, headers=tenant)
    assert r.status_code == 404, r.text
    d = r.json()["detail"]
    assert d["message"] == "That location doesn't exist any more. Pick another." and "no-such-location" in d["hint"]


@pytest.mark.asyncio
async def test_stock_adjust_with_warehouse_id_names_the_location(client, tenant):
    loc_id, whs, part_id = await _ctx(client, tenant)
    wh = next(w for w in whs if w.get("location_id") == loc_id)
    r = await client.post(f"/api/inventory/parts/{part_id}/adjust", json={
        "location_id": wh["id"], "qty": 2}, headers=tenant)
    assert r.status_code == 422, r.text
    d = r.json()["detail"]
    assert d["message"] == "Pick a location here, not a warehouse." and d["location_id"] == loc_id
    r = await client.post(f"/api/inventory/parts/{part_id}/adjust", json={
        "location_id": "nope", "qty": 2}, headers=tenant)
    assert r.status_code == 404, r.text
    ok = await client.post(f"/api/inventory/parts/{part_id}/adjust", json={
        "location_id": loc_id, "qty": 2}, headers=tenant)
    assert ok.status_code == 200, ok.text


@pytest.mark.asyncio
async def test_po_create_with_warehouse_id_is_422(client, tenant):
    loc_id, whs, part_id = await _ctx(client, tenant)
    sup = (await client.post("/api/suppliers", json={"name": "Parts Ltd"}, headers=tenant)).json()
    wh = next(w for w in whs if w.get("location_id") == loc_id)
    body = {"supplier_id": sup["id"], "location_id": wh["id"],
            "lines": [{"part_id": part_id, "qty_ordered": 1, "unit_cost": 5}]}
    r = await client.post("/api/inventory/po", json=body, headers=tenant)
    assert r.status_code == 422, r.text
    body["location_id"] = loc_id
    assert (await client.post("/api/inventory/po", json=body, headers=tenant)).status_code == 201


@pytest.mark.asyncio
async def test_tax_groups_and_exceptions_reject_foreign_tax(client, tenant, db):
    foreign = await _foreign_tax(db)
    r = await client.post("/api/settings/tax-groups", json={
        "name": "G", "member_tax_ids": [foreign]}, headers=tenant)
    assert r.status_code == 422, r.text
    r = await client.post("/api/settings/tax-exceptions", json={
        "doc_family": "tickets_sales", "target_type": "product", "target_id": "x",
        "tax_id": foreign}, headers=tenant)
    assert r.status_code == 422, r.text
    d = r.json()["detail"]
    assert d["unknown_ids"] == [foreign] and foreign not in d["message"]


@pytest.mark.asyncio
async def test_bulk_set_category_with_unknown_id_is_404(client, tenant):
    _, _, part_id = await _ctx(client, tenant)
    r = await client.post("/api/catalog/products/bulk", json={
        "ids": [part_id], "action": "category", "category_id": "no-such-cat"}, headers=tenant)
    assert r.status_code == 404, r.text
    assert r.json()["detail"]["message"] == "That category doesn't exist any more. Pick another."
    # clearing the category is still allowed
    r = await client.post("/api/catalog/products/bulk", json={
        "ids": [part_id], "action": "category", "category_id": None}, headers=tenant)
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_category_with_unknown_parent_is_404(client, tenant):
    r = await client.post("/api/catalog/categories", json={
        "name": "Screens", "parent_id": "no-such-parent"}, headers=tenant)
    assert r.status_code == 404, r.text


@pytest.mark.asyncio
async def test_inventory_bulk_set_category_with_unknown_id_is_404(client, tenant):
    _, _, part_id = await _ctx(client, tenant)
    r = await client.post("/api/inventory/parts/bulk", json={
        "part_ids": [part_id], "action": "set_category", "value": "no-such-cat"}, headers=tenant)
    assert r.status_code == 404, r.text
    cat = (await client.post("/api/catalog/categories", json={"name": "Screens"}, headers=tenant)).json()
    r = await client.post("/api/inventory/parts/bulk", json={
        "part_ids": [part_id], "action": "set_category", "value": cat["id"]}, headers=tenant)
    assert r.status_code == 200, r.text
