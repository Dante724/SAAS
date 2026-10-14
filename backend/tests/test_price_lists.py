"""Build Phase 3: price lists (price types) — location scope, item overrides,
per-doc-type org defaults, doc-aware resolution."""
import pytest


async def _pt(client, tenant, name, markup, **extra):
    body = {"name": name, "markup_pct": markup}
    body.update(extra)
    r = await client.post("/api/pricing/types", json=body, headers=tenant)
    assert r.status_code == 201, r.text
    return r.json()


@pytest.mark.asyncio
async def test_price_type_with_location_scope(client, tenant):
    pt = await _pt(client, tenant, "Wholesale", 15, location_scope="selected",
                   location_ids=["locA"])
    items = (await client.get("/api/pricing/types", headers=tenant)).json()["items"]
    row = next(x for x in items if x["id"] == pt["id"])
    assert row["location_scope"] == "selected" and row["location_ids"] == ["locA"]


@pytest.mark.asyncio
async def test_cost_x_markup_and_item_override(client, tenant, db):
    from backend.models import Part
    from backend.routers.pricing import resolve_price_for
    me = (await client.get("/api/auth/me", headers=tenant)).json()

    part = Part(org_id=me["org_id"], sku="P1", name="Widget", cost_price=100.0, sell_price=200.0)
    db.add(part)
    await db.commit()
    await db.refresh(part)

    pt = await _pt(client, tenant, "Retail", 50)     # 50% markup over cost
    # derived: 100 * 1.5 = 150
    derived = await resolve_price_for(db, me["org_id"], kind="product",
                                      ref_id=part.id, price_type_id=pt["id"])
    assert derived == pytest.approx(150.0)

    # explicit override wins
    ov = await client.put(f"/api/pricing/types/{pt['id']}/items",
                          json={"item_type": "product", "item_id": part.id, "price": 175.0},
                          headers=tenant)
    assert ov.status_code == 200
    overridden = await resolve_price_for(db, me["org_id"], kind="product",
                                         ref_id=part.id, price_type_id=pt["id"])
    assert overridden == pytest.approx(175.0)


@pytest.mark.asyncio
async def test_doc_type_defaults_resolution(client, tenant, db):
    from backend.models import Part
    from backend.routers.pricing import resolve_doc_price
    me = (await client.get("/api/auth/me", headers=tenant)).json()

    part = Part(org_id=me["org_id"], sku="P2", name="Gadget", cost_price=10.0, sell_price=20.0)
    db.add(part)
    await db.commit()
    await db.refresh(part)

    tickets_pt = await _pt(client, tenant, "TicketsList", 100)   # 2x
    sales_pt = await _pt(client, tenant, "SalesList", 300)       # 4x
    await client.put("/api/pricing/defaults",
                     json={"price_type_tickets_id": tickets_pt["id"],
                           "price_type_sales_id": sales_pt["id"]}, headers=tenant)

    tprice = await resolve_doc_price(db, me["org_id"], kind="product",
                                     ref_id=part.id, doc_type="tickets")
    sprice = await resolve_doc_price(db, me["org_id"], kind="product",
                                     ref_id=part.id, doc_type="sales")
    assert tprice == pytest.approx(20.0)    # 10 * 2
    assert sprice == pytest.approx(40.0)    # 10 * 4


@pytest.mark.asyncio
async def test_defaults_roundtrip_and_decimals(client, tenant):
    pt = await _pt(client, tenant, "Base2", 0)
    r = await client.put("/api/pricing/defaults",
                         json={"price_type_invoices_id": pt["id"], "price_decimals": 3},
                         headers=tenant)
    assert r.status_code == 200
    d = (await client.get("/api/pricing/defaults", headers=tenant)).json()
    assert d["price_type_invoices_id"] == pt["id"] and d["price_decimals"] == 3
