"""QA 2026-10-31 (B, med): coupons.

- a percentage coupon over 100% was accepted (the sale became free);
- on a taxed job the coupon came off the tax-inclusive total as a 0%-tax line, so the invoice
  still reported (and the shop remitted) the full tax;
- at the counter the coupon percentage was taken before the sale discount."""
import pytest


async def _taxed_order(client, h, price=100.0, rate=13.0, mode="imposed"):
    cid = (await client.post("/api/clients", json={"name": "C", "phone": "+15559"}, headers=h)).json()["id"]
    oid = (await client.post("/api/orders", json={"client_id": cid}, headers=h)).json()["id"]
    r = await client.post(f"/api/orders/{oid}/lines", headers=h, json={
        "line_type": "labour", "description": "Fix", "qty": 1, "unit_price": price,
        "tax_rate": rate, "tax_mode": mode})
    assert r.status_code in (200, 201), r.text
    return oid


async def _coupon(client, h, code, value, kind="pct"):
    return await client.post("/api/coupons", headers=h, json={
        "code": code, "kind": kind, "value": value, "min_total": 0, "max_uses": 5})


def _tax_of(line):
    """Tax inside one stored line (what the invoice totals block reports)."""
    total, rate = float(line["total"]), float(line["tax_rate"] or 0) / 100
    if not rate:
        return 0.0
    return total - total / (1 + rate)


@pytest.mark.asyncio
async def test_percent_coupon_over_100_is_refused(client, tenant):
    r = await _coupon(client, tenant, "FREE", 150)
    assert r.status_code == 422, r.text
    assert (await _coupon(client, tenant, "ALL", 100)).status_code == 201


@pytest.mark.asyncio
async def test_coupon_reduces_the_tax_on_a_taxed_job(client, tenant):
    oid = await _taxed_order(client, tenant)               # 100 + 13% = 113
    assert (await _coupon(client, tenant, "TEN", 10)).status_code == 201
    r = await client.post(f"/api/orders/{oid}/apply-coupon", headers=tenant, json={"code": "TEN"})
    assert r.status_code == 200, r.text
    assert r.json()["discount"] == pytest.approx(11.30)
    o = (await client.get(f"/api/orders/{oid}", headers=tenant)).json()
    assert o["total"] == pytest.approx(101.70)
    tax = sum(_tax_of(l) for l in o["lines"])
    assert tax == pytest.approx(11.70, abs=0.005)           # 13% of the 90 actually charged


@pytest.mark.asyncio
async def test_fixed_coupon_comes_off_the_pre_tax_price(client, tenant):
    oid = await _taxed_order(client, tenant)
    await _coupon(client, tenant, "FIVER", 10, kind="fixed")
    r = await client.post(f"/api/orders/{oid}/apply-coupon", headers=tenant, json={"code": "FIVER"})
    assert r.status_code == 200, r.text
    o = (await client.get(f"/api/orders/{oid}", headers=tenant)).json()
    assert o["total"] == pytest.approx(101.70)              # (100 - 10) × 1.13


@pytest.mark.asyncio
async def test_coupon_on_tax_included_job_takes_value_off_the_shelf_price(client, tenant):
    oid = await _taxed_order(client, tenant, price=105.0, rate=5.0, mode="included")
    await _coupon(client, tenant, "INC10", 10)
    r = await client.post(f"/api/orders/{oid}/apply-coupon", headers=tenant, json={"code": "INC10"})
    assert r.status_code == 200, r.text
    o = (await client.get(f"/api/orders/{oid}", headers=tenant)).json()
    assert o["total"] == pytest.approx(94.50)
    assert sum(_tax_of(l) for l in o["lines"]) == pytest.approx(4.50, abs=0.005)


@pytest.mark.asyncio
async def test_mixed_rates_coupon_stays_capped_and_reapply_refused(client, tenant):
    oid = await _taxed_order(client, tenant)
    await client.post(f"/api/orders/{oid}/lines", headers=tenant, json={
        "line_type": "part", "description": "Case", "qty": 1, "unit_price": 50, "tax_rate": 0})
    await _coupon(client, tenant, "MIX", 100)
    r = await client.post(f"/api/orders/{oid}/apply-coupon", headers=tenant, json={"code": "MIX"})
    assert r.status_code == 200, r.text
    o = (await client.get(f"/api/orders/{oid}", headers=tenant)).json()
    assert o["total"] == pytest.approx(0.0)
    assert r.json()["discount"] == pytest.approx(163.0)
    again = await client.post(f"/api/orders/{oid}/apply-coupon", headers=tenant, json={"code": "MIX"})
    assert again.status_code == 400, again.text


@pytest.mark.asyncio
async def test_pos_coupon_applies_after_the_sale_discount(client, tenant):
    await _coupon(client, tenant, "POS10", 10)
    r = await client.post("/api/sales", headers=tenant, json={
        "lines": [{"kind": "service", "ref_id": None, "description": "Fix", "qty": 1, "unit_price": 100}],
        "discount": 20, "coupon_code": "pos10"})
    assert r.status_code == 201, r.text
    assert r.json()["redeemed"]["coupon"] == pytest.approx(8.0)       # 10% of 80, not of 100
    assert r.json()["total"] == pytest.approx(72.0)


def test_printed_tax_summary_takes_the_coupon_tax_off():
    from types import SimpleNamespace as L

    from backend.routers.documents import _line_numbers
    work = L(qty=1, unit_price=100, tax_rate=13, tax_mode="imposed", discount=0, discount_type="pct", total=113)
    coupon = L(qty=1, unit_price=-10, tax_rate=13, tax_mode="imposed", discount=0, discount_type="pct", total=-11.3)
    assert sum(_line_numbers(l)["tax"] for l in (work, coupon)) == pytest.approx(11.70)
