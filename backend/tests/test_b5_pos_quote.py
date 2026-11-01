"""B5 — the POS charges exactly what its button says.

- POST /api/sales/quote prices the cart the same way POST /api/sales saves it (coupon, loyalty,
  line tax, gift card), and writes nothing.
- A gift card is a tender: it pays part of the total instead of discounting it.
- A charge sent with the total the cashier saw is refused if the server would save another one.
- Cash shows change due; a parked or unpaid sale can be paid later; items can be returned, which
  restocks them and refunds the customer's share through the refund document.
"""
import uuid

import pytest
from sqlalchemy import select


def _svc(price, qty=1, desc="Diagnostic"):
    return {"kind": "service", "ref_id": None, "description": desc, "qty": qty, "unit_price": price}


async def _till(client, H, name="Till", option="cash"):
    r = await client.post("/api/cash/accounts", json={"name": name, "payment_option": option}, headers=H)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _balance(client, H, aid):
    return next(a for a in (await client.get("/api/cash/accounts", headers=H)).json()["items"]
                if a["id"] == aid)["balance"]


async def _card(client, H, value):
    cid = (await client.post("/api/clients", json={"name": "Card holder"}, headers=H)).json()["id"]
    r = await client.post("/api/giftcards", json={"value": value, "client_id": cid}, headers=H)
    assert r.status_code == 201, r.text
    return r.json()


async def _sales_tax(client, H, rate, mode="imposed"):
    """A tax in the counter-sale stack (doc family tickets_sales)."""
    r = await client.post("/api/catalog/taxes", json={"name": f"VAT {rate}", "rate": rate, "mode": mode}, headers=H)
    assert r.status_code == 201, r.text
    tid = r.json()["id"]
    r = await client.put("/api/settings/tax-rules", json={"doc_family": "tickets_sales", "tax_ids": [tid]}, headers=H)
    assert r.status_code == 200, r.text
    return tid


async def _product(client, H, db, qty=5, price=40):
    from backend.models import Employee
    pid = (await client.post("/api/inventory/parts", json={
        "name": "Charger", "sku": f"CH-{uuid.uuid4().hex[:5]}", "sell_price": price, "cost_price": 10},
        headers=H)).json()["id"]
    me = (await client.get("/api/auth/me", headers=H)).json()
    loc = me.get("location_id") or (await client.get("/api/staff/locations", headers=H)).json()["items"][0]["id"]
    from backend.services import stock_rows
    await stock_rows.add(db, pid, loc, qty)
    await db.commit()
    return pid, loc


async def _stock(db, pid, loc):
    from backend.services import stock_rows
    db.expire_all()
    have, _ = await stock_rows.totals(db, pid, loc)
    return float(have)


# ── the reviewer's case ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_reviewer_case_quote_and_saved_sale_agree(client, tenant, db):
    """₹68 cart, ₹3 off and SAVE10: the button said ₹65, the sale saved ₹58.20."""
    from backend.models import Coupon
    await client.post("/api/coupons", json={"code": "SAVE10", "kind": "pct", "value": 10, "min_total": 0,
                                            "max_uses": 5}, headers=tenant)
    body = {"discount": 3, "coupon_code": "SAVE10", "lines": [_svc(40), _svc(28, desc="Case")]}
    q = await client.post("/api/sales/quote", json=body, headers=tenant)
    assert q.status_code == 200, q.text
    q = q.json()
    assert q["subtotal"] == pytest.approx(68)
    assert q["discount"] == pytest.approx(3)
    assert q["coupon"] == pytest.approx(6.8)
    assert q["total"] == pytest.approx(58.2)
    assert q["due"] == pytest.approx(58.2)
    assert q["problems"] == {}
    # A quote is a dry run: the coupon is not used up.
    db.expire_all()
    assert (await db.execute(select(Coupon.uses).where(Coupon.code == "SAVE10"))).scalar_one() == 0

    r = await client.post("/api/sales", json={**body, "expected_due": q["due"]}, headers=tenant)
    assert r.status_code == 201, r.text
    assert r.json()["total"] == pytest.approx(q["total"])
    assert r.json()["due"] == pytest.approx(q["due"])


@pytest.mark.asyncio
async def test_charge_refused_when_the_total_on_screen_is_stale(client, tenant):
    await client.post("/api/coupons", json={"code": "SAVE10", "kind": "pct", "value": 10, "min_total": 0}, headers=tenant)
    r = await client.post("/api/sales", json={"discount": 3, "coupon_code": "SAVE10", "expected_due": 65,
                                              "lines": [_svc(68)]}, headers=tenant)
    assert r.status_code == 409, r.text
    assert "58.20" in r.json()["detail"]
    assert (await client.get("/api/sales", headers=tenant)).json()["items"] == []


@pytest.mark.asyncio
async def test_quote_reports_a_bad_code_instead_of_failing(client, tenant):
    q = (await client.post("/api/sales/quote", json={"coupon_code": "NOPE", "gift_card_code": "GC-NOPE",
                                                     "lines": [_svc(50)]}, headers=tenant)).json()
    assert q["total"] == pytest.approx(50)
    assert q["coupon"] == 0 and q["gift_card"] == 0
    assert set(q["problems"]) == {"coupon", "gift_card"}
    # The charge itself still refuses an unknown code.
    assert (await client.post("/api/sales", json={"coupon_code": "NOPE", "lines": [_svc(50)]},
                              headers=tenant)).status_code == 404


# ── gift cards are a tender ──────────────────────────────────────

@pytest.mark.asyncio
async def test_gift_card_pays_part_of_the_sale_not_a_discount(client, tenant, db):
    from backend.models import Sale
    till = await _till(client, tenant)
    g = await _card(client, tenant, 30)
    q = (await client.post("/api/sales/quote", json={"gift_card_code": g["code"], "lines": [_svc(50)]},
                           headers=tenant)).json()
    assert (q["total"], q["gift_card"], q["due"], q["discount_total"]) == (50, 30, 20, 0)
    r = await client.post("/api/sales", json={"gift_card_code": g["code"], "account_id": till,
                                              "expected_due": 20, "lines": [_svc(50)]}, headers=tenant)
    assert r.status_code == 201, r.text
    s = (await db.execute(select(Sale).where(Sale.id == r.json()["id"]))).scalar_one()
    assert float(s.total) == 50 and float(s.discount) == 0 and s.paid is True
    assert await _balance(client, tenant, till) == pytest.approx(20)     # only the cash part
    assert (await client.get(f"/api/giftcards/{g['code']}", headers=tenant)).json()["balance"] == pytest.approx(0)


@pytest.mark.asyncio
async def test_gift_card_covering_everything_marks_the_sale_paid(client, tenant):
    g = await _card(client, tenant, 80)
    r = (await client.post("/api/sales", json={"gift_card_code": g["code"], "lines": [_svc(50)]},
                           headers=tenant)).json()
    assert r["paid"] is True and r["due"] == 0
    assert (await client.get(f"/api/giftcards/{g['code']}", headers=tenant)).json()["balance"] == pytest.approx(30)


# ── tax ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_line_tax_added_on_top_after_discounts(client, tenant):
    await _sales_tax(client, tenant, 10)
    body = {"discount": 20, "lines": [_svc(100), _svc(100, desc="Fit")]}
    q = (await client.post("/api/sales/quote", json=body, headers=tenant)).json()
    assert q["tax_total"] == pytest.approx(18)          # 10% of 180
    assert q["taxes"] == [{"name": "VAT 10", "rate": 10, "mode": "imposed", "amount": pytest.approx(18)}]
    assert q["total"] == pytest.approx(198)
    r = await client.post("/api/sales", json={**body, "expected_due": q["due"]}, headers=tenant)
    assert r.status_code == 201, r.text
    assert r.json()["total"] == pytest.approx(198)
    rc = (await client.get(f"/api/sales/{r.json()['id']}/receipt", headers=tenant)).json()
    assert rc["tax"] == pytest.approx(18)


@pytest.mark.asyncio
async def test_included_tax_is_shown_but_not_added(client, tenant):
    await _sales_tax(client, tenant, 20, mode="included")
    q = (await client.post("/api/sales/quote", json={"lines": [_svc(120)]}, headers=tenant)).json()
    assert q["total"] == pytest.approx(120)
    assert q["tax_total"] == 0
    assert q["tax_included"] == pytest.approx(20)


# ── cash and change due ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_cash_received_gives_change_due(client, tenant):
    till = await _till(client, tenant)
    q = (await client.post("/api/sales/quote", json={"cash_tendered": 100, "lines": [_svc(58.2)]},
                           headers=tenant)).json()
    assert q["change_due"] == pytest.approx(41.8)
    r = await client.post("/api/sales", json={"account_id": till, "cash_tendered": 100, "lines": [_svc(58.2)]},
                          headers=tenant)
    assert r.status_code == 201, r.text
    assert r.json()["change_due"] == pytest.approx(41.8)
    assert await _balance(client, tenant, till) == pytest.approx(58.2)   # the change never enters the till
    short = await client.post("/api/sales", json={"account_id": till, "cash_tendered": 50, "lines": [_svc(58.2)]},
                              headers=tenant)
    assert short.status_code == 400


# ── paying a parked or unpaid sale ───────────────────────────────

@pytest.mark.asyncio
async def test_pay_a_parked_sale_later(client, tenant):
    till = await _till(client, tenant)
    g = await _card(client, tenant, 10)
    s = (await client.post("/api/sales", json={"gift_card_code": g["code"], "lines": [_svc(45)]},
                           headers=tenant)).json()
    assert s["paid"] is False and s["due"] == pytest.approx(35)
    listed = next(x for x in (await client.get("/api/sales", headers=tenant)).json()["items"] if x["id"] == s["id"])
    assert listed["due"] == pytest.approx(35)
    r = await client.post(f"/api/sales/{s['id']}/pay", json={"account_id": till, "cash_tendered": 40}, headers=tenant)
    assert r.status_code == 200, r.text
    assert r.json()["paid"] is True and r.json()["change_due"] == pytest.approx(5)
    assert await _balance(client, tenant, till) == pytest.approx(35)
    assert (await client.post(f"/api/sales/{s['id']}/pay", json={"account_id": till}, headers=tenant)).status_code == 409


@pytest.mark.asyncio
async def test_reader_charges_only_what_the_gift_card_left(client, tenant):
    await client.post("/api/cash/accounts", json={"name": "Card", "payment_option": "card"}, headers=tenant)
    acc = [a for a in (await client.get("/api/cash/accounts", headers=tenant)).json()["items"] if a["name"] == "Card"][0]["id"]
    t = (await client.post("/api/payments/terminal/readers", json={"provider": "simulated", "label": "Desk"},
                           headers=tenant)).json()
    g = await _card(client, tenant, 10)
    sid = (await client.post("/api/sales", json={"gift_card_code": g["code"], "lines": [_svc(25)]},
                             headers=tenant)).json()["id"]
    ch = await client.post("/api/payments/terminal/charge", json={"terminal_id": t["id"], "sale_id": sid,
                                                                  "account_id": acc}, headers=tenant)
    assert ch.status_code in (200, 201), ch.text
    assert ch.json()["amount"] == pytest.approx(15)


# ── returns ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_return_restocks_and_refunds_the_share_paid(client, tenant, db):
    from backend.models import Refund
    till = await _till(client, tenant)
    pid, loc = await _product(client, tenant, db, qty=5, price=40)
    await client.post("/api/coupons", json={"code": "TEN", "kind": "fixed", "value": 10, "min_total": 0}, headers=tenant)
    s = (await client.post("/api/sales", json={"account_id": till, "coupon_code": "TEN", "lines": [
        {"kind": "product", "ref_id": pid, "description": "Charger", "qty": 2, "unit_price": 40},
        _svc(20, desc="Fit")]}, headers=tenant)).json()
    assert s["total"] == pytest.approx(90)
    assert await _stock(db, pid, loc) == 3
    detail = (await client.get(f"/api/sales/{s['id']}", headers=tenant)).json()
    line = next(ln for ln in detail["lines"] if ln["kind"] == "product")
    assert line["returnable"] == 2

    too_many = await client.post(f"/api/sales/{s['id']}/returns", json={
        "account_id": till, "lines": [{"line_id": line["id"], "qty": 3}]}, headers=tenant)
    assert too_many.status_code == 400

    r = await client.post(f"/api/sales/{s['id']}/returns", json={
        "account_id": till, "reason": "Wrong plug", "lines": [{"line_id": line["id"], "qty": 1}]}, headers=tenant)
    assert r.status_code == 201, r.text
    # One charger was 40 of a 100 sale that took 90: the customer gets 36 back.
    assert r.json()["refunded"] == pytest.approx(36)
    assert await _stock(db, pid, loc) == 4
    assert await _balance(client, tenant, till) == pytest.approx(54)
    ref = (await db.execute(select(Refund).where(Refund.id == r.json()["refund_id"]))).scalar_one()
    assert float(ref.amount) == pytest.approx(36) and ref.return_id == r.json()["return_id"]
    assert r.json()["refund_id"] in [x["id"] for x in (await client.get("/api/returns/refunds", headers=tenant)).json()["items"]]
    detail = (await client.get(f"/api/sales/{s['id']}", headers=tenant)).json()
    assert next(ln for ln in detail["lines"] if ln["kind"] == "product")["returnable"] == 1
    # Once part of a sale is returned, deleting it would put the stock and cash back twice.
    assert (await client.delete(f"/api/sales/{s['id']}", headers=tenant)).status_code == 409


@pytest.mark.asyncio
async def test_return_needs_a_paid_sale(client, tenant):
    s = (await client.post("/api/sales", json={"lines": [_svc(20)]}, headers=tenant)).json()
    till = await _till(client, tenant)
    line = (await client.get(f"/api/sales/{s['id']}", headers=tenant)).json()["lines"][0]
    r = await client.post(f"/api/sales/{s['id']}/returns", json={
        "account_id": till, "lines": [{"line_id": line["id"], "qty": 1}]}, headers=tenant)
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_return_gives_the_gift_card_share_back_to_the_card(client, tenant, db):
    till = await _till(client, tenant)
    g = await _card(client, tenant, 25)
    s = (await client.post("/api/sales", json={"account_id": till, "gift_card_code": g["code"],
                                               "lines": [_svc(50), _svc(50, desc="Fit")]}, headers=tenant)).json()
    assert await _balance(client, tenant, till) == pytest.approx(75)
    line = (await client.get(f"/api/sales/{s['id']}", headers=tenant)).json()["lines"][0]
    r = (await client.post(f"/api/sales/{s['id']}/returns", json={
        "account_id": till, "lines": [{"line_id": line["id"], "qty": 1}]}, headers=tenant)).json()
    assert r["refunded"] == pytest.approx(37.5) and r["gift_card"] == pytest.approx(12.5)
    assert await _balance(client, tenant, till) == pytest.approx(37.5)
    assert (await client.get(f"/api/giftcards/{g['code']}", headers=tenant)).json()["balance"] == pytest.approx(12.5)
