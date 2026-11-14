"""Audit 2026-11-12, P1 money findings (docs/audits/2026-11-12-backend-audit/03-money.md, SEC-02 in
01-tenancy-security.md). One regression test (or more) per finding; each failed before its fix.

- SEC-02 / FIN-05: a role limited to some cashboxes could read and move money in any other.
- FIN-03: deleting a gift card / coupon / points line kept the card spent, the coupon used, the
  points burned; the line's price could be edited away from its ledger.
- FIN-04: Invoices › Record payment took cash with no account (no till leg) and a closed shift.
- FIN-06: "Prevent backdating invoices" didn't apply to manual invoices (and compared wrong dates).
- FIN-07: Record payment locked only the invoice, not the jobs it bills (Take payment's lock).
- FIN-08: membership allowance checks took no lock on the plan.
- FIN-09: a gift card sold for money recorded no money; no liability figure.
- FIN-10: a cashbox holding money could be deactivated; its settings needed only "view finance".
- FIN-11: negative floats/expenses and future-dated manual money were accepted.
- FIN-12: closing a drawer or moving cash read the shift, then wrote, with no claim or lock.
- FIN-13: a transfer leg could be corrected alone; a correction double click posted twice.
- FIN-15: an online payment with no account landed in the first account, often the cash till.
"""
import asyncio
import os
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm.attributes import set_committed_value

from backend.models import (
    Account,
    CashDrawerMovement,
    CashShift,
    Coupon,
    GiftCard,
    LoyaltyLedger,
    Organisation,
    Transaction,
)
from backend.services.dates import shop_today

_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL", "")
pg_only = pytest.mark.skipif(not _URL.startswith("postgresql"), reason="real row-lock races need Postgres")


# ── helpers ─────────────────────────────────────────────────────────────────────────────────────

async def _me(client, h):
    return (await client.get("/api/auth/me", headers=h)).json()


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


async def _acct(client, h, name, option="cash"):
    r = await client.post("/api/cash/accounts", json={"name": name, "payment_option": option}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _balance(db, aid):
    db.expire_all()
    return float((await db.execute(select(Account.balance).where(Account.id == aid))).scalar_one() or 0)


async def _income(client, h, aid, amount):
    r = await client.post("/api/cash/transactions", json={"account_id": aid, "txn_type": "income",
                                                          "amount": amount}, headers=h)
    assert r.status_code == 201, r.text


async def _client(client, h, name="Pat"):
    return (await client.post("/api/clients", json={"name": name, "phone": "+15550101"}, headers=h)).json()["id"]


async def _job(client, h, cid=None, price=100):
    cid = cid or await _client(client, h)
    r = await client.post("/api/orders", headers=h, json={
        "client_id": cid, "complaint": "job",
        "lines": [{"line_type": "labour", "description": "Repair", "qty": 1, "unit_price": price}]})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def _order(client, h, oid):
    return (await client.get(f"/api/orders/{oid}", headers=h)).json()


async def _staff(client, h, *, account_ids=(), deny=()):
    r = await client.post("/api/settings/rbac/roles", json={
        "name": f"R{uuid.uuid4().hex[:5]}", "permissions": {p: False for p in deny},
        "template": "Full access", "account_ids": list(account_ids)}, headers=h)
    assert r.status_code in (200, 201), r.text
    email = f"s-{uuid.uuid4().hex[:6]}@t.dev"
    r = await client.post("/api/staff/employees", json={"name": "Staff", "email": email,
                                                        "password": "pw-test-12345", "role_id": r.json()["id"]},
                          headers=h)
    assert r.status_code == 201, r.text
    tok = (await client.post("/api/auth/token", data={"username": email, "password": "pw-test-12345"})).json()
    return {"Authorization": f"Bearer {tok['access_token']}"}


# ── SEC-02 / FIN-05: per-cashbox access ─────────────────────────────────────────────────────────

async def test_cashbox_restricted_role_cannot_read_or_move_money_in_other_accounts(client, tenant, db):
    till, safe = await _acct(client, tenant, "Front till"), await _acct(client, tenant, "Safe")
    await _income(client, tenant, till, 50)
    await _income(client, tenant, safe, 500)
    s = await _staff(client, tenant, account_ids=[till])

    # reads: only the till
    rows = (await client.get("/api/cash/transactions", headers=s)).json()["items"]
    assert rows and {r["account_id"] for r in rows} == {till}
    assert (await client.get(f"/api/cash/transactions?account_id={safe}", headers=s)).status_code == 403
    cols = (await client.get("/api/cash/total-money", headers=s)).json()["columns"]
    assert [a["id"] for c in cols for a in c["accounts"]] == [till]
    summ = (await client.get("/api/cash/summary", headers=s)).json()
    assert [a["id"] for a in summ["accounts"]] == [till] and summ["total_cash"] == 50

    # writes into the safe are refused, wherever they come from
    r = await client.post("/api/cash/transactions", json={"account_id": safe, "txn_type": "expense",
                                                          "amount": 100}, headers=s)
    assert r.status_code == 403, r.text
    r = await client.post("/api/cash/transfer", json={"from_account_id": safe, "to_account_id": till,
                                                      "amount": 100}, headers=s)
    assert r.status_code == 403, r.text
    oid = await _job(client, tenant)
    r = await client.post(f"/api/cash/orders/{oid}/pay", json={"method": "cash", "account_id": safe,
                                                               "amount": 40}, headers=s)
    assert r.status_code == 403, r.text
    inv = (await client.post("/api/finance/invoices", json={"order_id": oid}, headers=tenant)).json()["id"]
    r = await client.post(f"/api/finance/invoices/{inv}/pay", json={"method": "cash", "account_id": safe,
                                                                    "amount": 40}, headers=s)
    assert r.status_code == 403, r.text
    # a refund out of the safe (returns.py posts through the same choke point)
    r = await client.post(f"/api/cash/orders/{oid}/pay", json={"method": "cash", "account_id": till,
                                                               "amount": 40}, headers=tenant)
    assert r.status_code == 200, r.text
    r = await client.post("/api/returns/refunds", json={"account_id": safe, "amount": 10, "order_id": oid},
                          headers=s)
    assert r.status_code == 403, r.text
    assert await _balance(db, safe) == 500.0

    # their own till still works
    r = await client.post(f"/api/cash/orders/{oid}/pay", json={"method": "cash", "account_id": till,
                                                               "amount": 10}, headers=s)
    assert r.status_code == 200, r.text
    assert await _balance(db, till) == 100.0


async def test_restricted_role_cannot_void_a_leg_in_another_account(client, tenant, db):
    till, safe = await _acct(client, tenant, "Front till"), await _acct(client, tenant, "Safe")
    await _income(client, tenant, safe, 500)
    s = await _staff(client, tenant, account_ids=[till])
    leg = (await db.execute(select(Transaction.id).where(Transaction.account_id == safe))).scalar_one()
    assert (await client.delete(f"/api/cash/transactions/{leg}", headers=s)).status_code == 403
    assert await _balance(db, safe) == 500.0


# ── FIN-03: redemption lines give back what they spent ──────────────────────────────────────────

async def _line(client, h, oid, prefix):
    return next(ln for ln in (await _order(client, h, oid))["lines"] if ln["description"].startswith(prefix))


async def test_deleting_a_gift_card_line_restores_the_card(client, tenant, db):
    cid = await _client(client, tenant)
    code = (await client.post("/api/giftcards", json={"value": 60, "client_id": cid}, headers=tenant)).json()["code"]
    oid = await _job(client, tenant, cid)
    # a gift-card discount line from before FIN-09 (redemptions are payments now): line + ledger row
    from backend.models import GiftCardLedger, OrderLine
    g = (await db.execute(select(GiftCard).where(GiftCard.code == code))).scalar_one()
    g.balance = 0
    db.add(GiftCardLedger(org_id=g.org_id, gift_card_id=g.id, amount=-60, ref_type="order", ref_id=oid,
                          note="Redeemed"))
    db.add(OrderLine(order_id=oid, line_type="fee", kind="custom", line_role="giftcard", ref_id=g.id,
                     description=f"Gift card {code}", qty=1, unit_price=-60, total=-60))
    await db.commit()
    line = await _line(client, tenant, oid, "Gift card")

    # its price can't be edited away from the ledger
    r = await client.patch(f"/api/orders/{oid}/lines/{line['id']}", json={"qty": 2}, headers=tenant)
    assert r.status_code == 409, r.text

    r = await client.delete(f"/api/orders/{oid}/lines/{line['id']}", headers=tenant)
    assert r.status_code == 200 and r.json()["order_total"] == 100, r.text
    card = (await client.get(f"/api/giftcards/{code}", headers=tenant)).json()
    assert card["balance"] == 60, card
    # the card's ledger explains it, and still sums to the balance
    db.expire_all()
    g = (await db.execute(select(GiftCard).where(GiftCard.code == code))).scalar_one()
    amounts = (await db.execute(select(GiftCardLedger.amount).where(GiftCardLedger.gift_card_id == g.id))).scalars().all()
    assert sorted(float(a) for a in amounts) == [-60.0, 60.0, 60.0] and sum(amounts) == g.balance


async def test_deleting_a_coupon_line_gives_the_use_back(client, tenant, db):
    cid = await _client(client, tenant)
    r = await client.post("/api/coupons", json={"code": "ONCE10", "kind": "fixed", "value": 10, "max_uses": 1},
                          headers=tenant)
    assert r.status_code == 201, r.text
    oid = await _job(client, tenant, cid)
    assert (await client.post(f"/api/orders/{oid}/apply-coupon", json={"code": "once10"},
                              headers=tenant)).status_code == 200
    line = await _line(client, tenant, oid, "Coupon")
    r = await client.delete(f"/api/orders/{oid}/lines/{line['id']}", headers=tenant)
    assert r.status_code == 200, r.text
    db.expire_all()
    assert (await db.execute(select(Coupon.uses).where(Coupon.code == "ONCE10"))).scalar_one() == 0
    assert not [ln for ln in (await _order(client, tenant, oid))["lines"] if ln["description"].startswith("Coupon")]
    # usable again
    r = await client.post(f"/api/orders/{oid}/apply-coupon", json={"code": "ONCE10"}, headers=tenant)
    assert r.status_code == 200, r.text


async def test_deleting_a_loyalty_line_gives_the_points_back(client, tenant, db):
    me = await _me(client, tenant)
    await db.execute(update(Organisation).where(Organisation.id == me["org_id"]).values(loyalty_point_value=1.0))
    await db.commit()
    cid = await _client(client, tenant)
    db.add(LoyaltyLedger(org_id=me["org_id"], client_id=cid, points=100, ref_type="manual", note="seed"))
    await db.commit()
    oid = await _job(client, tenant, cid)
    r = await client.post(f"/api/orders/{oid}/redeem-loyalty", json={"points": 30}, headers=tenant)
    assert r.status_code == 200, r.text
    assert (await client.get(f"/api/clients/{cid}/loyalty", headers=tenant)).json()["points"] == 70
    line = await _line(client, tenant, oid, "Loyalty points")
    r = await client.delete(f"/api/orders/{oid}/lines/{line['id']}", headers=tenant)
    assert r.status_code == 200, r.text
    assert (await client.get(f"/api/clients/{cid}/loyalty", headers=tenant)).json()["points"] == 100


# ── FIN-04: Record payment needs an account and an open shift ───────────────────────────────────

async def test_invoice_cash_payment_needs_an_account_and_an_open_shift(client, tenant, db):
    till = await _acct(client, tenant, "Till")
    oid = await _job(client, tenant)
    inv = (await client.post("/api/finance/invoices", json={"order_id": oid}, headers=tenant)).json()["id"]
    r = await client.post(f"/api/finance/invoices/{inv}/pay", json={"method": "cash", "amount": 40}, headers=tenant)
    assert r.status_code == 400 and "account" in r.json()["detail"].lower(), r.text

    loc = await _loc(client, tenant)
    sid = (await client.post("/api/finance/shifts/open", json={"location_id": loc}, headers=tenant)).json()["id"]
    assert (await client.post(f"/api/finance/shifts/{sid}/close", json={"closing_float": 0},
                              headers=tenant)).status_code == 200
    r = await client.post(f"/api/finance/invoices/{inv}/pay", json={"method": "cash", "amount": 40,
                                                                    "account_id": till, "shift_id": sid},
                          headers=tenant)
    assert r.status_code == 409 and "closed" in r.json()["detail"].lower(), r.text
    # nothing was recorded by either refusal
    assert (await client.get(f"/api/finance/invoices/{inv}", headers=tenant)).json()["amount_paid"] == 0
    # with a till and no stale shift it goes through, and the till holds it
    r = await client.post(f"/api/finance/invoices/{inv}/pay", json={"method": "cash", "amount": 40,
                                                                    "account_id": till}, headers=tenant)
    assert r.status_code == 200, r.text
    assert await _balance(db, till) == 40.0


# ── FIN-06: prevent backdating applies to manual invoices ───────────────────────────────────────

async def test_prevent_backdating_covers_manual_invoices(client, tenant, db):
    me = await _me(client, tenant)
    await db.execute(update(Organisation).where(Organisation.id == me["org_id"])
                     .values(prevent_backdating_invoices=True))
    await db.commit()
    today = await shop_today(db, me["org_id"])
    cid = await _client(client, tenant)
    body = {"client_id": cid, "lines": [{"description": "Work", "qty": 1, "unit_price": 50}]}

    r = await client.post("/api/finance/invoices", json={**body, "issued_at": today.isoformat()}, headers=tenant)
    assert r.status_code == 201, r.text
    r = await client.post("/api/finance/invoices", json={**body, "issued_at": (today - timedelta(days=30)).isoformat()},
                          headers=tenant)
    assert r.status_code == 422 and "backdating" in r.json()["detail"].lower(), r.text
    r = await client.post("/api/finance/invoices", json={**body, "issued_at": (today + timedelta(days=3)).isoformat()},
                          headers=tenant)
    assert r.status_code == 422, r.text
    # an invoice made today by taking a payment isn't blocked by the manual one dated today
    till = await _acct(client, tenant, "Till")
    oid = await _job(client, tenant, cid)
    r = await client.post(f"/api/cash/orders/{oid}/pay", json={"method": "cash", "account_id": till, "amount": 10},
                          headers=tenant)
    assert r.status_code == 200, r.text


async def test_backdating_check_is_off_when_the_setting_is_off(client, tenant, db):
    cid = await _client(client, tenant)
    me = await _me(client, tenant)
    today = await shop_today(db, me["org_id"])
    r = await client.post("/api/finance/invoices", json={
        "client_id": cid, "issued_at": (today - timedelta(days=30)).isoformat(),
        "lines": [{"description": "Work", "qty": 1, "unit_price": 50}]}, headers=tenant)
    assert r.status_code == 201, r.text


# ── FIN-07: Record payment takes the job's lock first ───────────────────────────────────────────

async def test_invoice_payment_locks_the_jobs_it_bills(client, tenant, monkeypatch):
    from backend.services import invoice_money
    oid = await _job(client, tenant)
    inv = (await client.post("/api/finance/invoices", json={"order_id": oid}, headers=tenant)).json()["id"]
    till = await _acct(client, tenant, "Till")
    locked, real = [], invoice_money.lock_order

    async def spy(db, order_id):
        locked.append(order_id)
        await real(db, order_id)
    monkeypatch.setattr(invoice_money, "lock_order", spy)
    r = await client.post(f"/api/finance/invoices/{inv}/pay", json={"method": "cash", "amount": 30,
                                                                    "account_id": till}, headers=tenant)
    assert r.status_code == 200, r.text
    assert oid in locked


@pg_only
async def test_invoice_payment_and_job_payment_cannot_both_fill_one_balance(client, tenant):
    till = await _acct(client, tenant, "Till")
    oid = await _job(client, tenant)
    inv = (await client.post("/api/finance/invoices", json={"order_id": oid}, headers=tenant)).json()["id"]
    a, b = await asyncio.gather(
        client.post(f"/api/finance/invoices/{inv}/pay", json={"method": "cash", "amount": 100, "account_id": till},
                    headers=tenant),
        client.post(f"/api/cash/orders/{oid}/pay", json={"method": "cash", "amount": 100, "account_id": till},
                    headers=tenant))
    assert sorted([a.status_code, b.status_code])[0] == 200
    assert sorted([a.status_code, b.status_code])[1] in (400, 409), (a.text, b.text)
    assert (await client.get(f"/api/finance/invoices/{inv}", headers=tenant)).json()["amount_paid"] == 100


# ── FIN-08: the plan's allowance is used under the plan's lock ──────────────────────────────────

async def test_membership_coverage_locks_the_plan(client, tenant, monkeypatch):
    from backend.services import memberships as msvc
    oil = (await client.post("/api/catalog/services", json={"name": "Oil", "price": 80}, headers=tenant)).json()["id"]
    plan = (await client.post("/api/memberships/plans", json={
        "name": "Care", "price_yearly": 100, "items": [{"kind": "service", "service_id": oil, "qty": 1}]},
        headers=tenant)).json()
    cid = await _client(client, tenant)
    boat = (await client.post("/api/assets", json={"owner_type": "client", "client_id": cid, "brand": "B",
                                                   "model": "M"}, headers=tenant)).json()["id"]
    m = (await client.post("/api/memberships", json={"plan_id": plan["id"], "client_id": cid, "device_id": boat,
                                                     "billing": "yearly"}, headers=tenant)).json()
    locked, real = [], msvc.lock_memberships

    async def spy(db, ids):
        locked.extend(ids)
        await real(db, ids)
    monkeypatch.setattr(msvc, "lock_memberships", spy)
    oid = (await client.post("/api/orders", json={"client_id": cid, "device_id": boat, "complaint": "x"},
                             headers=tenant)).json()["id"]
    r = await client.post(f"/api/orders/{oid}/lines", json={"qty": 1, "kind": "service", "ref_id": oil,
                                                            "description": "Oil", "unit_price": 80}, headers=tenant)
    assert r.status_code == 201, r.text
    o = await _order(client, tenant, oid)
    assert o["lines"][0]["discount"] == 100      # covered by the plan…
    assert m["id"] in locked                     # …while holding the plan's lock


@pg_only
async def test_two_jobs_cannot_both_use_the_last_allowance(client, tenant):
    oil = (await client.post("/api/catalog/services", json={"name": "Oil", "price": 80}, headers=tenant)).json()["id"]
    plan = (await client.post("/api/memberships/plans", json={
        "name": "Care", "price_yearly": 100, "items": [{"kind": "service", "service_id": oil, "qty": 1}]},
        headers=tenant)).json()
    cid = await _client(client, tenant)
    boat = (await client.post("/api/assets", json={"owner_type": "client", "client_id": cid, "brand": "B",
                                                   "model": "M"}, headers=tenant)).json()["id"]
    await client.post("/api/memberships", json={"plan_id": plan["id"], "client_id": cid, "device_id": boat,
                                                "billing": "yearly"}, headers=tenant)
    jobs = [(await client.post("/api/orders", json={"client_id": cid, "device_id": boat, "complaint": "x"},
                               headers=tenant)).json()["id"] for _ in range(2)]
    await asyncio.gather(*[client.post(f"/api/orders/{j}/lines", json={
        "qty": 1, "kind": "service", "ref_id": oil, "description": "Oil", "unit_price": 80}, headers=tenant)
        for j in jobs])
    covered = [ln for j in jobs for ln in (await _order(client, tenant, j))["lines"] if ln["discount"] == 100]
    assert len(covered) == 1


# ── FIN-09: a gift card sold for money ──────────────────────────────────────────────────────────

async def test_gift_card_sold_for_cash_takes_the_money_and_shows_as_a_liability(client, tenant, db):
    till = await _acct(client, tenant, "Till")
    cid = await _client(client, tenant)
    r = await client.post("/api/giftcards", json={"value": 60, "client_id": cid, "account_id": till}, headers=tenant)
    assert r.status_code == 201, r.text
    code = r.json()["code"]
    assert await _balance(db, till) == 60.0
    leg = (await db.execute(select(Transaction).where(Transaction.account_id == till))).scalar_one()
    assert leg.ref_type == "gift_card"
    # the money can't be taken out of Cash alone while the card stays spendable
    assert (await client.delete(f"/api/cash/transactions/{leg.id}", headers=tenant)).status_code == 409
    liab = (await client.get("/api/giftcards/liability", headers=tenant)).json()
    assert liab["outstanding"] == 60 and liab["cards"] == 1
    oid = await _job(client, tenant, cid)
    await client.post(f"/api/orders/{oid}/redeem-giftcard", json={"code": code, "amount": 25}, headers=tenant)
    assert (await client.get("/api/giftcards/liability", headers=tenant)).json()["outstanding"] == 35
    # a promotional card (no account) still moves no money
    r = await client.post("/api/giftcards", json={"value": 20, "client_id": cid}, headers=tenant)
    assert r.status_code == 201 and await _balance(db, till) == 60.0


# ── FIN-10: cashbox settings and deactivation ───────────────────────────────────────────────────

async def test_cashbox_with_money_cannot_be_deactivated(client, tenant, db):
    till, bank = await _acct(client, tenant, "Till"), await _acct(client, tenant, "Bank", "cashless")
    await _income(client, tenant, till, 500)
    r = await client.delete(f"/api/cash/accounts/{till}", headers=tenant)
    assert r.status_code == 409 and "500.00" in r.json()["detail"], r.text
    assert (await client.post("/api/cash/transfer", json={"from_account_id": till, "to_account_id": bank,
                                                          "amount": 500}, headers=tenant)).status_code == 201
    assert (await client.delete(f"/api/cash/accounts/{till}", headers=tenant)).status_code == 200


async def test_cashbox_settings_need_the_manage_accounts_right(client, tenant, db):
    till = await _acct(client, tenant, "Till")
    viewer = await _staff(client, tenant, deny=("can_manage_accounts",))
    r = await client.put(f"/api/cash/accounts/{till}", json={"name": "Till", "payment_option": "card",
                                                             "fee_pct": 5}, headers=viewer)
    assert r.status_code == 403, r.text
    db.expire_all()
    assert (await db.execute(select(Account.payment_option).where(Account.id == till))).scalar_one() == "cash"


# ── FIN-11: amount validation ───────────────────────────────────────────────────────────────────

async def test_money_inputs_refuse_negative_and_future_values(client, tenant, db):
    loc = await _loc(client, tenant)
    r = await client.post("/api/finance/shifts/open", json={"location_id": loc, "opening_float": -50}, headers=tenant)
    assert r.status_code == 422, r.text
    r = await client.post("/api/finance/expenses", json={"description": "x", "amount": -1000}, headers=tenant)
    assert r.status_code == 422, r.text
    till = await _acct(client, tenant, "Till")
    me = await _me(client, tenant)
    tomorrow = (await shop_today(db, me["org_id"]) + timedelta(days=1)).isoformat()
    r = await client.post("/api/cash/transactions", json={"account_id": till, "txn_type": "income", "amount": 10,
                                                          "actual_date": tomorrow}, headers=tenant)
    assert r.status_code == 422, r.text
    oid = await _job(client, tenant)
    r = await client.post(f"/api/cash/orders/{oid}/prepay", json={"amount": 1e12, "account_id": till}, headers=tenant)
    assert r.status_code == 422, r.text
    sid = (await client.post("/api/finance/shifts/open", json={"location_id": loc}, headers=tenant)).json()["id"]
    r = await client.post(f"/api/finance/shifts/{sid}/close", json={"closing_float": -5}, headers=tenant)
    assert r.status_code == 422, r.text
    assert await _balance(db, till) == 0.0


# ── FIN-12: close and movement claim the shift ──────────────────────────────────────────────────

async def _stale_shift(monkeypatch):
    """Make the routes read the shift as it was before a concurrent close committed (closed_at
    still empty in memory), the window the race opened on Postgres."""
    from backend.routers import finance
    real = finance._shift_or_404

    async def stale(db, org_id, shift_id):
        s = await real(db, org_id, shift_id)
        set_committed_value(s, "closed_at", None)
        return s
    monkeypatch.setattr(finance, "_shift_or_404", stale)


async def test_a_second_close_cannot_overwrite_the_count(client, tenant, db, monkeypatch):
    loc = await _loc(client, tenant)
    sid = (await client.post("/api/finance/shifts/open", json={"location_id": loc, "opening_float": 100},
                             headers=tenant)).json()["id"]
    assert (await client.post(f"/api/finance/shifts/{sid}/close", json={"closing_float": 100},
                              headers=tenant)).status_code == 200
    await _stale_shift(monkeypatch)
    r = await client.post(f"/api/finance/shifts/{sid}/close", json={"closing_float": 999}, headers=tenant)
    assert r.status_code == 409, r.text
    db.expire_all()
    assert float((await db.execute(select(CashShift.closing_float).where(CashShift.id == sid))).scalar_one()) == 100


async def test_a_movement_cannot_land_on_a_drawer_closed_meanwhile(client, tenant, db, monkeypatch):
    loc = await _loc(client, tenant)
    sid = (await client.post("/api/finance/shifts/open", json={"location_id": loc}, headers=tenant)).json()["id"]
    assert (await client.post(f"/api/finance/shifts/{sid}/close", json={"closing_float": 0},
                              headers=tenant)).status_code == 200
    await _stale_shift(monkeypatch)
    r = await client.post(f"/api/finance/shifts/{sid}/movement", json={"direction": "in", "amount": 40,
                                                                      "note": "float"}, headers=tenant)
    assert r.status_code == 409, r.text
    assert not (await db.execute(select(CashDrawerMovement.id).where(CashDrawerMovement.shift_id == sid))).first()


# ── FIN-13: corrections ─────────────────────────────────────────────────────────────────────────

async def test_a_transfer_leg_cannot_be_corrected_alone(client, tenant, db):
    a, b = await _acct(client, tenant, "A"), await _acct(client, tenant, "B")
    await _income(client, tenant, a, 100)
    assert (await client.post("/api/cash/transfer", json={"from_account_id": a, "to_account_id": b, "amount": 40},
                              headers=tenant)).status_code == 201
    leg = (await db.execute(select(Transaction.id).where(Transaction.account_id == b))).scalar_one()
    r = await client.post(f"/api/cash/transactions/{leg}/correct", json={"delta": -10, "reason": "typo"},
                          headers=tenant)
    assert r.status_code == 409 and "transfer" in r.json()["detail"].lower(), r.text
    assert await _balance(db, a) == 60.0 and await _balance(db, b) == 40.0


async def test_a_correction_posts_once(client, tenant, db):
    till = await _acct(client, tenant, "Till")
    await _income(client, tenant, till, 100)
    t = (await db.execute(select(Transaction.id).where(Transaction.account_id == till))).scalar_one()
    body = {"delta": -10, "reason": "miscount"}
    # a retry with the same key gets the first answer
    k = {**tenant, "Idempotency-Key": "corr-1"}
    r1 = await client.post(f"/api/cash/transactions/{t}/correct", json=body, headers=k)
    r2 = await client.post(f"/api/cash/transactions/{t}/correct", json=body, headers=k)
    assert r1.status_code == 201 and r2.json() == r1.json(), (r1.text, r2.text)
    assert await _balance(db, till) == 90.0
    # without a key, the same correction a moment later is a double click
    r3 = await client.post(f"/api/cash/transactions/{t}/correct", json=body, headers=tenant)
    assert r3.status_code == 409, r3.text
    assert await _balance(db, till) == 90.0


# ── FIN-15: online money with no account lands outside the cash till ───────────────────────────

async def test_online_payment_without_account_does_not_land_in_the_cash_till(client, tenant, db):
    till = await _acct(client, tenant, "Till")
    oid = await _job(client, tenant)
    r = await client.post("/api/payments/checkout", json={"order_id": oid, "amount": 30}, headers=tenant)
    assert r.status_code == 201, r.text
    r = await client.post(f"/api/payments/intents/{r.json()['id']}/simulate-pay")
    assert r.status_code == 200 and r.json()["status"] == "succeeded", r.text
    assert await _balance(db, till) == 0.0
    db.expire_all()
    online = (await db.execute(select(Account).where(Account.balance == 30))).scalars().all()
    assert len(online) == 1 and online[0].payment_option != "cash", [(a.name, a.payment_option) for a in online]
