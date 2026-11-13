"""Audit DB-08 / FIN-14 — money, rates, percentages, points and quantities are exact end to end.

* Every money column reads back as an exact Decimal (``money.Exact``); none is mapped
  ``asdecimal=False`` any more.
* The money-affecting rates, percentages, points and quantities that were binary ``Float`` are
  ``NUMERIC`` now; only measures (mm, kg, GPS, hours, meter readings, thresholds) stay Float.
* ``Exact`` lifts a float operand through its decimal text, so code that still does float maths on
  a column value gets an exact result instead of a TypeError or a binary-noise penny.
* The API still answers with JSON numbers.
"""
import json
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import Float, Numeric, select

from backend.services.money import Exact, d2, dsum, json_dumps

pytestmark = pytest.mark.asyncio

# Floats that are measures, not money: they stay binary floats on purpose.
# order_lines.booked_hours (flat-rate hours billed, 144av #2) is listed under quantities in DB-08 but
# is a time measure: the money it drives is computed into the line's NUMERIC price/total, which is
# what gets stored and summed. Decided in the PR #222 re-review (FIN-14 b); keep it Float.
MEASURES = {
    "document_templates.page_w_mm", "document_templates.page_h_mm", "document_templates.margin_mm",
    "label_templates.width_mm", "label_templates.height_mm",
    "parts.weight", "storage_bins.max_weight_capacity",
    "time_entries.gps_lat", "time_entries.gps_lng",
    "checkins.reading_value",
    "employees.weekly_hours", "shifts.hours_worked", "order_lines.booked_hours",
    "custom_fields.gate_threshold", "custom_fields.upsell_threshold",
    "checklist_template_items.upsell_threshold", "order_checklist_items.upsell_threshold",
}


def _columns():
    import backend.models  # noqa: F401  (registers every table)
    from backend.db.database import Base
    for t in Base.metadata.sorted_tables:
        for c in t.columns:
            yield f"{t.name}.{c.name}", c.type


async def test_no_money_column_reads_as_float():
    floaty = [n for n, ty in _columns() if isinstance(ty, Numeric) and not isinstance(ty, Float)
              and not ty.asdecimal]
    assert floaty == [], f"Numeric columns still mapped asdecimal=False: {floaty}"


async def test_only_measures_stay_binary_float():
    left = sorted(n for n, ty in _columns() if isinstance(ty, Float) and n not in MEASURES)
    assert left == [], f"money-affecting columns still Float: {left}"


async def test_exact_lifts_floats_through_their_text():
    p = Exact("19.99")
    assert p * 3 == Decimal("59.97") and isinstance(p * 3, Exact)
    assert p + 0.01 == Decimal("20.00") and 0.01 + p == Decimal("20.00")   # float on either side
    assert Exact("0.1") + 0.2 == Decimal("0.3")                              # not 0.30000000000000004
    assert Exact("100.00") * 0.0825 == Decimal("8.25")
    assert Exact("10.00") - 0.1 - 0.2 == Decimal("9.70")
    assert 1 - Exact("0.25") == Decimal("0.75") and isinstance(1 - Exact("0.25"), Exact)
    assert Exact("10") / 4 == Decimal("2.5") and 10 / Exact("4") == Decimal("2.5")
    assert Exact("30.01") == 30.01 and not (Exact("30.01") < 30.01) and Exact("30.01") >= 30.01
    assert 30.01 <= Exact("30.01") and max(Exact("0.00"), -0.01) == 0
    assert sum([Exact("0.1")] * 3) == Decimal("0.3")
    assert -Exact("1.5") == Decimal("-1.5") and isinstance(abs(Exact("-1.5")), Exact)
    assert Decimal("1.10") + Exact("0.20") == Decimal("1.30")
    assert float(Exact("2.675")) == 2.675 and round(Exact("2.675"), 2) == Decimal("2.68")   # half-up
    assert round(Exact("2.5")) == 3 and d2(Exact("1.005")) == Decimal("1.01")
    assert Exact(0.1) == Decimal("0.1")                                       # built from float text
    with pytest.raises(ZeroDivisionError):
        Exact("1") / 0


async def test_exact_writes_as_a_json_number():
    assert json.loads(json_dumps({"a": Exact("10.50"), "b": Exact("7")})) == {"a": 10.5, "b": 7}


# ── through the API and the database ─────────────────────────────────────────
async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


async def _job(client, h, lines):
    loc = await _loc(client, h)
    cid = (await client.post("/api/clients", json={"name": f"D {uuid.uuid4().hex[:4]}"}, headers=h)).json()["id"]
    r = await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x",
                                               "is_estimate": False, "lines": []}, headers=h)
    assert r.status_code in (200, 201), r.text
    oid = r.json()["id"]
    for ln in lines:
        r = await client.post(f"/api/orders/{oid}/lines", headers=h, json={
            "line_type": "labour", "kind": "custom", "description": "Work", "qty": 1, **ln})
        assert r.status_code in (200, 201), r.text
    return oid


async def test_order_money_reads_exact_and_answers_numbers(client, tenant, db):
    from backend.models import Order, OrderLine
    oid = await _job(client, tenant, [{"unit_price": 0.1}, {"unit_price": 0.2}, {"unit_price": 19.99, "qty": 3}])
    o = (await client.get(f"/api/orders/{oid}", headers=tenant)).json()
    assert o["total"] == 60.27 and isinstance(o["total"], float)
    for ln in o["lines"]:
        assert isinstance(ln["unit_price"], (int, float)) and isinstance(ln["total"], (int, float))
        assert isinstance(ln["qty"], (int, float)) and not isinstance(ln["qty"], str)
    order = (await db.execute(select(Order).where(Order.id == oid))).scalar_one()
    assert isinstance(order.total, Exact) and order.total == Decimal("60.27")
    lines = (await db.execute(select(OrderLine).where(OrderLine.order_id == oid))).scalars().all()
    assert all(isinstance(x.unit_price, Exact) and isinstance(x.qty, Exact) for x in lines)
    assert dsum(x.total for x in lines) == order.total


async def test_tax_on_many_lines_reconciles_to_the_cent(client, tenant, db):
    from backend.models import Order
    # 7 × 0.99 at 8.25% imposed: per-line tax is 0.08 (0.081675 half-up); the job's tax is the sum
    # of its lines and the total is subtotal + tax, to the cent.
    oid = await _job(client, tenant, [{"unit_price": 0.99, "tax_rate": 8.25, "tax_mode": "imposed"}] * 7)
    o = (await client.get(f"/api/orders/{oid}", headers=tenant)).json()
    order = (await db.execute(select(Order).where(Order.id == oid))).scalar_one()
    line_tax = dsum(d2(Decimal("0.99") * Decimal("0.0825")) for _ in range(7))
    assert order.tax == line_tax == Decimal("0.56") and isinstance(order.tax, Exact)
    assert order.total == Decimal("6.93") + line_tax and o["total"] == 7.49


async def test_split_payments_settle_exactly(client, tenant, db):
    from backend.models import Order, Payment
    oid = await _job(client, tenant, [{"unit_price": 99.99}])
    acc = (await client.get("/api/cash/accounts", headers=tenant)).json()["items"][0]["id"]
    for amt in (33.33, 33.34, 33.32):   # equal amounts back to back trip the double-tap guard
        r = await client.post(f"/api/cash/orders/{oid}/pay", headers=tenant,
                              json={"amount": amt, "account_id": acc, "method": "card"})
        assert r.status_code in (200, 201), r.text
    order = (await db.execute(select(Order).where(Order.id == oid))).scalar_one()
    paid = (await db.execute(select(Payment.amount).where(Payment.order_id == oid))).scalars().all()
    assert all(isinstance(x, Exact) for x in paid) and len(paid) == 3
    assert dsum(paid) == Decimal("99.99") and order.total - dsum(paid) == 0


async def test_pos_discount_and_split_tender_to_the_cent(client, tenant):
    await client.post("/api/coupons", json={"code": "DB08", "kind": "pct", "value": 12.5, "min_total": 0,
                                            "max_uses": 5}, headers=tenant)
    lines = [{"kind": "service", "ref_id": None, "description": "Fix", "qty": 3, "unit_price": 3.33}]
    r = await client.post("/api/sales", json={"lines": lines, "coupon_code": "db08"}, headers=tenant)
    assert r.status_code == 201, r.text
    b = r.json()
    # 9.99 − 12.5% (1.24875 → 1.25) = 8.74
    assert b["total"] == 8.74 and isinstance(b["total"], float)
    assert b["redeemed"]["coupon"] == 1.25


async def test_stock_used_in_tenths_lands_on_zero(client, tenant, db):
    """DB-08's failure: 0.1 m of cable used 30 times left 2.9999999999999996 on hand."""
    from backend.models import Part, StockLevel
    from backend.services.money import add_to_column
    loc = await _loc(client, tenant)
    r = await client.post("/api/inventory/parts", json={"name": f"Cable {uuid.uuid4().hex[:4]}", "uom": "m",
                                              "sell_price": 1, "cost_price": 0.5}, headers=tenant)
    assert r.status_code in (200, 201), r.text
    part = (await db.execute(select(Part).where(Part.id == r.json()["id"]))).scalar_one()
    lvl = StockLevel(org_id=part.org_id, part_id=part.id, location_id=loc, qty_available=3)
    db.add(lvl)
    await db.commit()
    for _ in range(30):
        await add_to_column(db, lvl, "qty_available", -0.1, round_to=None)
    await db.commit()
    await db.refresh(lvl)
    assert lvl.qty_available == 0 and isinstance(lvl.qty_available, Exact)


async def test_rates_and_percentages_are_exact(client, tenant, db):
    from backend.models import Employee, OrderLine
    oid = await _job(client, tenant, [{"unit_price": 10, "tax_rate": 8.875, "tax_mode": "imposed"}])
    ln = (await db.execute(select(OrderLine).where(OrderLine.order_id == oid))).scalar_one()
    assert ln.tax_rate == Decimal("8.875") and isinstance(ln.tax_rate, Exact)
    emp = (await db.execute(select(Employee).limit(1))).scalar_one()
    emp.commission_labour = 12.345
    await db.commit()
    await db.refresh(emp)
    assert emp.commission_labour == Decimal("12.345")


async def test_exact_prints_like_the_float_it_replaced():
    """Receipts, exports and messages were written against floats: a NUMERIC(14,4) quantity of 2
    still prints "2.0" / "2", and a fixed-point format rounds half-up."""
    q, m = Exact("2.0000"), Exact("10.50")
    assert (str(q), f"{q}", f"{q:g}", str(m), f"{m:.2f}") == ("2.0", "2.0", "2", "10.5", "10.50")
    assert f"{Exact('2.675'):.2f}" == "2.68" and f"{Exact('1234.5'):,.2f}" == "1,234.50"
    assert repr(m) == "Exact('10.50')" and str(Exact("16000.12345678")) == "16000.12345678"
