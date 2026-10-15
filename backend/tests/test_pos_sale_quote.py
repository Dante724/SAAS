"""POST /api/sales/quote previews exactly what POST /api/sales charges (before rewards), with the
tax broken down per tax, and writes nothing."""
import pytest
from sqlalchemy import func, select

from backend.models import Sale, StockLevel, StockMovement


async def _tax(client, h, name, rate, mode="imposed", **extra):
    r = await client.post("/api/catalog/taxes", json={"name": name, "rate": rate, "mode": mode, **extra},
                          headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _stack(client, h, *tax_ids):
    r = await client.put("/api/settings/tax-rules",
                         json={"doc_family": "tickets_sales", "tax_ids": list(tax_ids)}, headers=h)
    assert r.status_code == 200, r.text


async def _main_loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


async def _quote(client, h, lines, discount=0):
    r = await client.post("/api/sales/quote", json={"lines": lines, "discount": discount}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


async def _sell(client, h, lines, discount=0):
    r = await client.post("/api/sales", json={"lines": lines, "discount": discount}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


async def _assert_quote_matches_sale(client, h, lines, discount=0):
    q = await _quote(client, h, lines, discount)
    s = await _sell(client, h, lines, discount)
    assert q["subtotal"] == pytest.approx(s["subtotal"])
    assert q["tax_total"] == pytest.approx(s["tax_total"])
    assert q["total"] == pytest.approx(s["total"])
    assert sum(t["amount"] for t in q["taxes"]) == pytest.approx(q["tax_total"])
    return q


_SVC = {"kind": "service", "description": "Screen fit", "qty": 1, "unit_price": 100}


@pytest.mark.asyncio
async def test_quote_breaks_out_gst_and_pst_and_matches_sale(client, tenant):
    main = await _main_loc(client, tenant)
    gst = await _tax(client, tenant, "GST", 5.0)
    pst = await _tax(client, tenant, "PST", 7.0, location_scope="selected", location_ids=[main])
    await _stack(client, tenant, gst, pst)

    q = await _assert_quote_matches_sale(client, tenant, [_SVC, {**_SVC, "qty": 2, "unit_price": 25}])
    assert q["subtotal"] == pytest.approx(150.0)
    assert [(t["name"], t["rate"], t["mode"]) for t in q["taxes"]] == [
        ("GST", 5.0, "imposed"), ("PST", 7.0, "imposed")]
    assert {t["name"]: t["amount"] for t in q["taxes"]} == {"GST": 7.5, "PST": 10.5}
    assert q["tax_imposed"] == pytest.approx(18.0)
    assert q["total"] == pytest.approx(168.0)


@pytest.mark.asyncio
async def test_quote_discount_exception_and_scope_match_sale(client, tenant):
    other = (await client.post("/api/staff/locations", json={"name": "Branch"}, headers=tenant)).json()["id"]
    gst = await _tax(client, tenant, "GST", 5.0)
    pst = await _tax(client, tenant, "PST", 7.0, location_scope="selected", location_ids=[other])
    await _stack(client, tenant, gst, pst)
    svc = (await client.post("/api/catalog/services", json={"name": "Exempt work", "price": 50},
                             headers=tenant)).json()["id"]
    r = await client.post("/api/settings/tax-exceptions", json={
        "doc_family": "tickets_sales", "target_type": "service", "target_id": svc, "exempt": True},
        headers=tenant)
    assert r.status_code == 201, r.text

    lines = [{**_SVC, "discount": 10},
             {"kind": "service", "ref_id": svc, "description": "Exempt", "qty": 1, "unit_price": 50}]
    q = await _assert_quote_matches_sale(client, tenant, lines, discount=14)
    # PST is scoped to another branch; the exempt line carries none; the discount lowers the base
    assert [t["name"] for t in q["taxes"]] == ["GST"]
    assert q["tax_total"] == pytest.approx(4.05)      # 5% of 90 × (140−14)/140
    assert q["total"] == pytest.approx(130.05)


@pytest.mark.asyncio
async def test_quote_included_tax_matches_sale(client, tenant):
    hst = await _tax(client, tenant, "HST incl", 13.0, mode="included")
    await _stack(client, tenant, hst)
    q = await _assert_quote_matches_sale(client, tenant, [{**_SVC, "unit_price": 113}])
    assert q["tax_total"] == pytest.approx(13.0) and q["tax_imposed"] == 0
    assert q["total"] == pytest.approx(113.0)


@pytest.mark.asyncio
async def test_quote_has_no_side_effects(client, tenant, db):
    gst = await _tax(client, tenant, "GST", 5.0)
    await _stack(client, tenant, gst)
    loc = await _main_loc(client, tenant)
    part = (await client.post("/api/inventory/parts", json={
        "name": "Cable", "sku": "CBL-Q", "barcode": "CBL-Q", "sell_price": 20}, headers=tenant)).json()
    db.add(StockLevel(part_id=part["id"], location_id=loc, qty_available=5, qty_reserved=0))
    await db.commit()
    lines = [{"kind": "product", "ref_id": part["id"], "description": "Cable", "qty": 2, "unit_price": 20}]

    q = await _quote(client, tenant, lines)
    assert q["total"] == pytest.approx(42.0)
    db.expire_all()
    assert (await db.execute(select(func.count()).select_from(Sale).where(
        Sale.location_id == loc))).scalar_one() == 0
    sl = (await db.execute(select(StockLevel).where(StockLevel.part_id == part["id"]))).scalar_one()
    assert sl.qty_available == 5
    assert (await db.execute(select(func.count()).select_from(StockMovement).where(
        StockMovement.part_id == part["id"]))).scalar_one() == 0

    # the first real sale still gets the first number — the quote didn't consume one
    s = await _sell(client, tenant, lines)
    assert s["total"] == pytest.approx(q["total"])
    assert s["sale_number"] == "S-00001"


@pytest.mark.asyncio
async def test_quote_empty_cart_and_unknown_product(client, tenant):
    q = await _quote(client, tenant, [])
    assert q == {"subtotal": 0, "discount": 0, "taxes": [], "tax_total": 0, "tax_imposed": 0, "total": 0}
    r = await client.post("/api/sales/quote", json={"lines": [
        {"kind": "product", "ref_id": "nope", "description": "Ghost", "qty": 1, "unit_price": 1}]},
        headers=tenant)
    assert r.status_code == 404
