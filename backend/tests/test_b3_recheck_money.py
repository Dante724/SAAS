"""B3/B5 recheck: client credit spent more than once, a reader-paid counter sale refunded twice,
a counter-sale cash return missing from the till close, and a sale line's stored tax ignoring a
category tax exception. Each test replays the reviewer's case through the API."""
import pytest
from sqlalchemy import select

from backend.models import Category, Part, SaleLine
from backend.tests.test_b3_money_blockers import _client, _client_balance, _close, _ctx, _job, _loc
from backend.tests.test_b5_pos_quote import _balance, _sales_tax, _till
from backend.tests.test_opf_card_readers import _card_account, _charge, _reader


async def _advance(client, h, cid, amount):
    r = await client.post(f"/api/clients/{cid}/balance", json={"kind": "advance", "amount": amount}, headers=h)
    assert r.status_code in (200, 201), r.text


async def _invoice(client, h, cid, price):
    r = await client.post("/api/finance/invoices", headers=h, json={
        "client_id": cid, "lines": [{"description": "Repair", "qty": 1, "unit_price": price}]})
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ── R2b: one advance, two open jobs ──────────────────────────────────────────
@pytest.mark.asyncio
async def test_credit_spent_on_an_open_job_cannot_pay_another(client, tenant):
    cid = await _client(client, tenant)
    await _advance(client, tenant, cid, 100)
    a, b = await _job(client, tenant, cid, 80), await _job(client, tenant, cid, 80)
    r = await client.post(f"/api/cash/orders/{a}/pay", json={"method": "balance", "amount": 80}, headers=tenant)
    assert r.status_code == 200, r.text
    r = await client.post(f"/api/cash/orders/{b}/pay", json={"method": "balance", "amount": 80}, headers=tenant)
    assert r.status_code == 400 and "20.00" in r.json()["detail"], r.text
    # the 20 that is left can still be used
    r = await client.post(f"/api/cash/orders/{b}/pay", json={"method": "balance", "amount": 20}, headers=tenant)
    assert r.status_code == 200, r.text
    await _close(client, tenant, a)
    await _close(client, tenant, b)
    assert (await _ctx(client, tenant, b))["remaining"] == pytest.approx(60)
    assert await _client_balance(client, tenant, cid) == pytest.approx(60)    # owes the 60 still due on b


@pytest.mark.asyncio
async def test_cash_on_an_open_job_is_not_credit(client, tenant):
    cash = await _till(client, tenant)
    cid = await _client(client, tenant)
    a, b = await _job(client, tenant, cid, 80), await _job(client, tenant, cid, 80)
    r = await client.post(f"/api/cash/orders/{a}/pay", json={"method": "cash", "amount": 80, "account_id": cash},
                          headers=tenant)
    assert r.status_code == 200, r.text
    r = await client.post(f"/api/cash/orders/{b}/pay", json={"method": "balance", "amount": 80}, headers=tenant)
    assert r.status_code == 400, r.text


# ── R2c: one advance, two standalone invoices ────────────────────────────────
@pytest.mark.asyncio
async def test_standalone_invoices_use_the_credit_up(client, tenant):
    cid = await _client(client, tenant)
    await _advance(client, tenant, cid, 100)
    one, two = await _invoice(client, tenant, cid, 80), await _invoice(client, tenant, cid, 80)
    r = await client.post(f"/api/finance/invoices/{one}/pay", json={"method": "balance", "amount": 80}, headers=tenant)
    assert r.status_code == 200 and r.json()["status"] == "paid", r.text
    assert await _client_balance(client, tenant, cid) == pytest.approx(-20)
    r = await client.post(f"/api/finance/invoices/{two}/pay", json={"method": "balance", "amount": 80}, headers=tenant)
    assert r.status_code == 400, r.text
    assert await _client_balance(client, tenant, cid) == pytest.approx(-20)


@pytest.mark.asyncio
async def test_cash_for_a_standalone_invoice_is_not_credit(client, tenant):
    cash = await _till(client, tenant)
    cid = await _client(client, tenant)
    inv = await _invoice(client, tenant, cid, 80)
    r = await client.post(f"/api/finance/invoices/{inv}/pay", headers=tenant,
                          json={"method": "cash", "amount": 100, "account_id": cash, "overpay": "refund"})
    assert r.status_code == 200 and r.json()["status"] == "paid", r.text
    assert await _client_balance(client, tenant, cid) == pytest.approx(0)
    inv2 = await _invoice(client, tenant, cid, 50)
    r = await client.post(f"/api/finance/invoices/{inv2}/pay", headers=tenant,
                          json={"method": "cash", "amount": 70, "account_id": cash, "overpay": "keep_balance"})
    assert r.status_code == 200, r.text
    assert await _client_balance(client, tenant, cid) == pytest.approx(-20)   # only the kept 20 is credit
    oid = await _job(client, tenant, cid, 50)
    await _close(client, tenant, oid)
    r = await client.post(f"/api/cash/orders/{oid}/pay", json={"method": "balance", "amount": 50}, headers=tenant)
    assert r.status_code == 400 and "20.00" in r.json()["detail"], r.text


# ── R6a / R6b: a reader-paid counter sale, a till return and a reader refund ─
async def _reader_paid_sale(client, h, price=100):
    till = await _till(client, h)
    acc = await _card_account(client, h)
    t = await _reader(client, h, provider="simulated")
    r = await client.post("/api/sales", json={"lines": [
        {"kind": "service", "ref_id": None, "description": "Screen fit", "qty": 1, "unit_price": price}]}, headers=h)
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    ch = (await _charge(client, h, t["id"], sale_id=sid, account_id=acc)).json()
    r = await client.post(f"/api/payments/terminal/charges/{ch['id']}/simulate", json={"outcome": "approve"}, headers=h)
    assert r.json()["status"] == "succeeded", r.text
    line = (await client.get(f"/api/sales/{sid}", headers=h)).json()["lines"][0]["id"]
    return till, sid, ch["id"], line


@pytest.mark.asyncio
async def test_till_return_then_reader_refund_is_capped(client, tenant):
    till, sid, chid, line = await _reader_paid_sale(client, tenant)
    r = await client.post(f"/api/sales/{sid}/returns", json={"account_id": till, "lines": [{"line_id": line, "qty": 1}]},
                          headers=tenant)
    assert r.status_code == 201 and r.json()["refunded"] == pytest.approx(100), r.text
    r = await client.post(f"/api/payments/terminal/charges/{chid}/refund", json={}, headers=tenant)
    assert r.status_code == 422, r.text
    assert await _balance(client, tenant, till) == pytest.approx(-100)


@pytest.mark.asyncio
async def test_reader_refund_then_till_return_gives_no_more_money(client, tenant):
    till, sid, chid, line = await _reader_paid_sale(client, tenant)
    r = await client.post(f"/api/payments/terminal/charges/{chid}/refund", json={"amount": 60}, headers=tenant)
    assert r.status_code == 200, r.text
    r = await client.post(f"/api/sales/{sid}/returns", json={"account_id": till, "lines": [{"line_id": line, "qty": 1}]},
                          headers=tenant)
    assert r.status_code == 201 and r.json()["refunded"] == pytest.approx(40), r.text   # 60 already on the card
    assert await _balance(client, tenant, till) == pytest.approx(-40)
    r = await client.post(f"/api/payments/terminal/charges/{chid}/refund", json={"amount": 1}, headers=tenant)
    assert r.status_code == 422, r.text


# ── R7b: a counter-sale cash return comes out of the drawer at close ─────────
@pytest.mark.asyncio
async def test_cash_return_of_a_sale_is_taken_off_the_till_close(client, tenant):
    loc = await _loc(client, tenant)
    till = (await client.post("/api/cash/accounts", json={"name": "Till", "payment_option": "cash", "location_id": loc},
                              headers=tenant)).json()["id"]
    shift = (await client.post("/api/finance/shifts/open", json={"location_id": loc, "opening_float": 50},
                               headers=tenant)).json()["id"]
    s = await client.post("/api/sales", json={"account_id": till, "lines": [
        {"kind": "service", "ref_id": None, "description": "Screen fit", "qty": 1, "unit_price": 80}]}, headers=tenant)
    assert s.status_code == 201, s.text
    line = (await client.get(f"/api/sales/{s.json()['id']}", headers=tenant)).json()["lines"][0]["id"]
    r = await client.post(f"/api/sales/{s.json()['id']}/returns",
                          json={"account_id": till, "lines": [{"line_id": line, "qty": 1}]}, headers=tenant)
    assert r.status_code == 201, r.text
    r = await client.post(f"/api/finance/shifts/{shift}/close?closing_float=50", headers=tenant)
    assert r.status_code == 200, r.text
    assert r.json()["expected_cash"] == pytest.approx(50) and r.json()["discrepancy"] == 0, r.json()


# ── B5: a sale line's stored tax follows a category tax exception ────────────
@pytest.mark.asyncio
async def test_sale_line_tax_follows_the_category_exception(client, tenant, db):
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    await _sales_tax(client, tenant, 10)
    cat = Category(org_id=me["org_id"], entity_type="product", name="Exempt goods")
    db.add(cat)
    await db.commit()
    r = await client.post("/api/settings/tax-exceptions", headers=tenant, json={
        "doc_family": "tickets_sales", "target_type": "category", "target_id": cat.id, "exempt": True})
    assert r.status_code in (200, 201), r.text
    pid = (await client.post("/api/inventory/parts", json={"name": "Book", "sku": "BK-1", "sell_price": 50,
                                                          "cost_price": 10}, headers=tenant)).json()["id"]
    part = (await db.execute(select(Part).where(Part.id == pid))).scalar_one()
    part.category_id = cat.id
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    from backend.services import stock_rows
    await stock_rows.add(db, pid, loc, 3)
    await db.commit()
    till = await _till(client, tenant)
    s = await client.post("/api/sales", json={"account_id": till, "lines": [
        {"kind": "product", "ref_id": pid, "description": "Book", "qty": 1, "unit_price": 50}]}, headers=tenant)
    assert s.status_code == 201, s.text
    assert s.json()["total"] == pytest.approx(50)              # the exemption priced the sale
    db.expire_all()
    ln = (await db.execute(select(SaleLine).where(SaleLine.sale_id == s.json()["id"]))).scalar_one()
    assert ln.tax == 0 and ln.tax_rate == 0
