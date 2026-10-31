"""Audit R4 — money as Decimal, pinned to the cent.

Columns moved off asdecimal=False read back as Decimal; arithmetic on them goes through
services/money.py (dec/d2/dsum), and the API still answers with JSON numbers, exactly as before.
"""
import json
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from backend.services.money import d2, dec, dsum, json_default, json_dumps, msum, q2

pytestmark = pytest.mark.asyncio


# ── helpers: round-half-up, exact sums ───────────────────────────────────────
async def test_rounding_is_half_up_to_the_cent():
    assert q2(2.675) == 2.68 and d2(2.675) == Decimal("2.68")      # round() gives 2.67
    assert q2(1.005) == 1.01                                       # float repr penny
    assert q2(0.125) == 0.13 and q2(0.135) == 0.14                 # never half-to-even
    assert q2(-2.675) == -2.68                                     # half away from zero
    assert d2(Decimal("10.005")) == Decimal("10.01")
    assert d2(None) == Decimal("0.00")


async def test_sums_are_exact():
    assert dsum([0.1, 0.2]) == Decimal("0.30")
    assert msum([0.1, 0.2]) == 0.3
    assert dsum([Decimal("19.99")] * 3 + [0.03]) == Decimal("60.00")
    assert dsum([]) == Decimal("0.00")


async def test_dec_lifts_without_rounding():
    assert dec(0.1) == Decimal("0.1")                    # the float's text, not its binary noise
    assert dec(None) == 0 and dec("") == 0
    x = Decimal("3.335")
    assert dec(x) is x
    assert d2(dec(3) * dec(3.335)) == Decimal("10.01")   # float 3*3.335 is 10.004999… → 10.00


async def test_json_writes_decimal_as_a_number():
    assert json.loads(json_dumps({"a": Decimal("10.50"), "b": Decimal("7"), "c": [Decimal("0.10")]})) == \
        {"a": 10.5, "b": 7, "c": [0.1]}
    assert json.loads(json_dumps({"x": Decimal("NaN")})) == {"x": None}
    with pytest.raises(TypeError):
        json_default(object())


# ── API: flipped columns keep answering with numbers ─────────────────────────
async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


async def test_drawer_expected_cash_is_exact(client, tenant, db):
    from backend.models import CashShift
    loc = await _loc(client, tenant)
    r = await client.post("/api/finance/shifts/open", json={"location_id": loc, "opening_float": 100.10},
                          headers=tenant)
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    for direction, amt in (("in", 0.10), ("in", 0.20), ("out", 0.05)):
        r = await client.post(f"/api/finance/shifts/{sid}/movement",
                              json={"direction": direction, "amount": amt, "note": "float check"}, headers=tenant)
        assert r.status_code == 201, r.text
    r = await client.post(f"/api/finance/shifts/{sid}/close", json={"closing_float": 100.35}, headers=tenant)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["expected_cash"] == 100.35 and isinstance(body["expected_cash"], float)
    assert body["discrepancy"] == 0.0 and "explanation" not in body
    shift = (await db.execute(select(CashShift).where(CashShift.id == sid))).scalar_one()
    assert isinstance(shift.expected_cash, Decimal) and shift.expected_cash == Decimal("100.35")
    assert isinstance(shift.opening_float, Decimal)
    hist = (await client.get("/api/finance/shifts", headers=tenant)).json()["items"]
    row = next(s for s in hist if s["id"] == sid)
    assert row["opening_float"] == 100.1 and row["closing_float"] == 100.35 and row["discrepancy"] == 0.0


async def test_drawer_short_by_a_cent(client, tenant):
    loc = await _loc(client, tenant)
    sid = (await client.post("/api/finance/shifts/open", json={"location_id": loc, "opening_float": 0.30},
                             headers=tenant)).json()["id"]
    r = await client.post(f"/api/finance/shifts/{sid}/close", json={"closing_float": 0.29}, headers=tenant)
    assert r.json()["discrepancy"] == -0.01 and r.json()["explanation"]["direction"] == "short"


async def test_gift_card_redeem_to_the_cent(client, tenant, db):
    from backend.models import GiftCard, GiftCardLedger
    cid = (await client.post("/api/clients", json={"name": f"G {uuid.uuid4().hex[:4]}"}, headers=tenant)).json()["id"]
    r = await client.post("/api/giftcards", json={"value": 50, "client_id": cid}, headers=tenant)
    assert r.status_code == 201, r.text
    code = r.json()["code"]
    assert r.json()["balance"] == 50 and r.json()["initial_value"] == 50
    loc = await _loc(client, tenant)
    oid = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x"},
                             headers=tenant)).json()["id"]
    r = await client.post(f"/api/orders/{oid}/lines", headers=tenant, json={
        "line_type": "part", "kind": "custom", "description": "Screen", "qty": 1, "unit_price": 80})
    assert r.status_code in (200, 201), r.text
    r = await client.post(f"/api/orders/{oid}/redeem-giftcard", json={"code": code, "amount": 19.99}, headers=tenant)
    assert r.status_code == 200, r.text
    assert r.json()["redeemed"] == 19.99 and r.json()["card_balance"] == 30.01
    g = (await db.execute(select(GiftCard).where(GiftCard.code == code))).scalar_one()
    assert g.balance == Decimal("30.01") and isinstance(g.initial_value, Decimal)
    led = (await db.execute(select(GiftCardLedger).where(GiftCardLedger.gift_card_id == g.id))).scalars().all()
    assert dsum(x.amount for x in led) == Decimal("30.01")
    card = (await client.get(f"/api/giftcards/{code}", headers=tenant)).json()
    assert card["balance"] == 30.01 and isinstance(card["balance"], float)
    # Paying off exactly what's left works: 30.01 (float) > Decimal("30.01") is True in Python,
    # so the check must compare Decimals.
    r = await client.post(f"/api/orders/{oid}/redeem-giftcard", json={"code": code, "amount": 30.01}, headers=tenant)
    assert r.status_code == 200, r.text
    assert r.json()["card_balance"] == 0.0
    # More than the card holds is refused, to the cent.
    r = await client.post(f"/api/orders/{oid}/redeem-giftcard", json={"code": code, "amount": 0.01}, headers=tenant)
    assert r.status_code == 400 and "0.00" in r.text


async def test_expenses_and_payouts_stay_numbers(client, tenant):
    for amt in (0.1, 0.2):
        r = await client.post("/api/finance/expenses", json={"description": "Tape", "amount": amt}, headers=tenant)
        assert r.status_code == 201, r.text
    s = (await client.get("/api/finance/reports/summary", headers=tenant)).json()
    assert s["total_expenses"] == 0.3 and s["gross_profit"] == -0.3
    r = await client.post("/api/finance/payouts", json={"gross_amount": 100.10, "fee_amount": 2.95}, headers=tenant)
    assert r.status_code == 201, r.text
    assert r.json()["net_amount"] == 97.15
    p = (await client.get("/api/finance/payouts", headers=tenant)).json()["items"][0]
    assert (p["gross_amount"], p["fee_amount"], p["net_amount"]) == (100.1, 2.95, 97.15)
    assert all(isinstance(p[k], float) for k in ("gross_amount", "fee_amount", "net_amount"))


async def test_json_column_accepts_decimal(db, client, tenant):
    """The engine's JSON serializer writes Decimal money (e.g. an audit detail built from a
    Decimal column) as a number instead of failing the commit."""
    from backend.models import CashShift
    loc = await _loc(client, tenant)
    sid = (await client.post("/api/finance/shifts/open", json={"location_id": loc}, headers=tenant)).json()["id"]
    shift = (await db.execute(select(CashShift).where(CashShift.id == sid))).scalar_one()
    shift.open_denoms = {"note": Decimal("20.00"), "count": Decimal("3")}
    await db.commit()
    await db.refresh(shift)
    assert shift.open_denoms == {"note": 20.0, "count": 3}


async def test_cash_rounding_step_is_half_up():
    from backend.services.money import round_to_step
    assert round_to_step(10.025, 0.05) == 10.05      # round(10.025 / 0.05) * 0.05 gave 10.0
    assert round_to_step(10.024, 0.05) == 10.0
    assert round_to_step(10.075, 0.05) == 10.1
    assert round_to_step(Decimal("19.99"), 0.05) == 20.0
    assert round_to_step(-10.025, 0.05) == -10.05


async def _order_with_lines(client, h, *prices):
    loc = await _loc(client, h)
    cid = (await client.post("/api/clients", json={"name": "Lines"}, headers=h)).json()["id"]
    oid = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x"},
                             headers=h)).json()["id"]
    for p in prices:
        r = await client.post(f"/api/orders/{oid}/lines", headers=h, json={
            "line_type": "part", "kind": "custom", "description": "Bit", "qty": 1, "unit_price": p})
        assert r.status_code == 201, r.text
    return oid


async def test_order_total_is_an_exact_sum(client, tenant):
    oid = await _order_with_lines(client, tenant, 0.1, 0.2, 0.7)
    o = (await client.get(f"/api/orders/{oid}", headers=tenant)).json()
    assert o["total"] == 1.0 and o["parts_total"] == 1.0          # not 0.9999999999999999


async def test_order_cash_rounding_half_up(client, tenant, db):
    from backend.models import Employee, Organisation
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    org_id = (await db.execute(select(Employee.org_id).where(Employee.id == me["id"]))).scalar_one()
    org = (await db.execute(select(Organisation).where(Organisation.id == org_id))).scalar_one()
    org.rounding_enabled = True
    await db.commit()
    oid = await _order_with_lines(client, tenant, 10.025)
    assert (await client.get(f"/api/orders/{oid}", headers=tenant)).json()["total"] == 10.05
