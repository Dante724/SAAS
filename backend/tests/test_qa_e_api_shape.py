"""QA fix E6 — additive API-shape fixes; every old field stays.

Found in the 5-shop QA run (31 Oct 2026): PO create answered only `po_id`; the portal link only
`portal_token` (the track link says `token`); product search ignored `q=`; a recommendation
with urgency "later" got a 422 that didn't list the allowed values; a contact marked
is_supplier used on a PO gave a bare "Supplier not found".
"""
import pytest


async def _loc_part(client, h):
    locs = (await client.get("/api/staff/locations", headers=h)).json()
    loc = (locs["items"] if isinstance(locs, dict) else locs)[0]["id"]
    part = (await client.post("/api/inventory/parts", json={"name": "OLED screen", "sku": "OLED-1"},
                              headers=h)).json()["id"]
    return loc, part


@pytest.mark.asyncio
async def test_po_create_returns_id_and_po_id(client, tenant):
    loc, part = await _loc_part(client, tenant)
    sup = (await client.post("/api/suppliers", json={"name": "Parts Ltd"}, headers=tenant)).json()
    r = await client.post("/api/inventory/po", json={"supplier_id": sup["id"], "location_id": loc,
                                                     "lines": [{"part_id": part, "qty_ordered": 1}]}, headers=tenant)
    assert r.status_code == 201, r.text
    assert r.json()["id"] == r.json()["po_id"]


@pytest.mark.asyncio
async def test_contact_marked_supplier_on_po_names_the_supplier_id(client, tenant):
    loc, part = await _loc_part(client, tenant)
    c = (await client.post("/api/clients", json={"name": "Wholesale Co", "is_supplier": True},
                           headers=tenant)).json()
    r = await client.post("/api/inventory/po", json={"supplier_id": c["id"], "location_id": loc,
                                                     "lines": [{"part_id": part, "qty_ordered": 1}]}, headers=tenant)
    assert r.status_code == 404, r.text
    detail = r.json()["detail"]
    assert detail["message"] == "Pick Wholesale Co from the supplier list."
    assert "/api/suppliers" in detail["hint"]
    sups = (await client.get("/api/suppliers", headers=tenant)).json()
    sups = sups.get("items", sups) if isinstance(sups, dict) else sups
    mirror = next(s for s in sups if s["name"] == "Wholesale Co")
    assert detail["supplier_id"] == mirror["id"]


@pytest.mark.asyncio
async def test_portal_link_also_returns_token(client, tenant):
    c = (await client.post("/api/clients", json={"name": "Jess"}, headers=tenant)).json()
    r = await client.post(f"/api/clients/{c['id']}/portal-link", headers=tenant)
    assert r.status_code == 200, r.text
    assert r.json()["token"] == r.json()["portal_token"]


@pytest.mark.asyncio
async def test_product_search_accepts_q(client, tenant):
    for n in ("OLED screen", "Battery"):
        await client.post("/api/catalog/products", json={"name": n}, headers=tenant)
    items = (await client.get("/api/catalog/products", params={"q": "oled"}, headers=tenant)).json()
    items = items.get("items", items) if isinstance(items, dict) else items
    assert [p["name"] for p in items] == ["OLED screen"]
    parts = (await client.get("/api/inventory/parts", params={"q": "batt"}, headers=tenant)).json()
    parts = parts.get("items", parts) if isinstance(parts, dict) else parts
    assert [p["name"] for p in parts] == ["Battery"]


@pytest.mark.asyncio
async def test_recommendation_urgency_later_and_helpful_422(client, tenant):
    c = (await client.post("/api/clients", json={"name": "Rec Co"}, headers=tenant)).json()
    o = (await client.post("/api/orders", json={"client_id": c["id"], "complaint": "x"}, headers=tenant)).json()
    base = {"title": "Replace battery", "unit_price": 50, "kind": "service"}
    r = await client.post(f"/api/orders/{o['id']}/recommendations", json={**base, "urgency": "high"}, headers=tenant)
    assert r.status_code == 422 and "now, soon, later" in r.json()["detail"], r.text
    r = await client.post(f"/api/orders/{o['id']}/recommendations", json={**base, "urgency": "later"}, headers=tenant)
    assert r.status_code == 201, r.text
    assert r.json()["urgency"] == "soon"
