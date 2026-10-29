"""Launch blockers B3 and B7: the money numbers must agree with each other.

Each test replays one reviewer case, then runs `_reconcile`, which checks the four views of the
same money agree: the client's ledger balance, the job's balance due, its invoice status, the
cashbox balances and the P&L.

Sign convention everywhere: client balance > 0 means the client owes the shop, < 0 means the
shop holds the client's credit.
"""
from datetime import date

import httpx
import pytest
from sqlalchemy import select

from backend.config import settings
from backend.models import Invoice, Order, Payment
from backend.tests.test_opf_card_readers import (  # noqa: F401  (the stripe fixture is used by name)
    _charge,
    _reader,
    stripe,
)


# ── helpers ──────────────────────────────────────────────────────────────────
async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


async def _client(client, h, name="Dana"):
    return (await client.post("/api/clients", json={"name": name, "phone": "+15550100"}, headers=h)).json()["id"]


async def _job(client, h, cid, price=100.0):
    oid = (await client.post("/api/orders", json={"client_id": cid, "location_id": await _loc(client, h)},
                             headers=h)).json()["id"]
    r = await client.post(f"/api/orders/{oid}/lines", json={
        "line_type": "labour", "description": "Repair", "qty": 1, "unit_price": price}, headers=h)
    assert r.status_code == 201, r.text
    return oid


async def _account(client, h, kind="cash", name=None):
    r = await client.post("/api/cash/accounts", json={"name": name or f"{kind.title()} box", "payment_option": kind},
                          headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _close(client, h, oid):
    r = await client.patch(f"/api/orders/{oid}/status", json={"status": "done"}, headers=h)
    assert r.status_code == 200, r.text


async def _ctx(client, h, oid):
    return (await client.get(f"/api/cash/orders/{oid}/payment-context", headers=h)).json()


async def _client_balance(client, h, cid):
    return (await client.get(f"/api/clients/{cid}/balance", headers=h)).json()["balance"]


async def _accounts_total(client, h):
    return round(sum(a["balance"] or 0 for a in (await client.get("/api/cash/accounts", headers=h)).json()["items"]), 2)


async def _invoices(db, oid):
    db.expire_all()
    return (await db.execute(select(Invoice).where(Invoice.order_id == oid))).scalars().all()


async def _reconcile(client, h, db, *, cid, oid, credit=0.0):
    """Client ledger, job balance, invoice status and till agree for a client with one job.
    `credit` is client money held beyond this job (an advance); the till holds every payment."""
    ctx = await _ctx(client, h, oid)
    due, over = ctx["remaining"], ctx["overpaid"]
    bal = await _client_balance(client, h, cid)
    assert bal == pytest.approx(due - over - credit), (bal, ctx)
    for inv in await _invoices(db, oid):
        if inv.status in ("void", "cancelled", "canceled"):
            continue
        assert (inv.status == "paid") == (due == 0), (inv.status, inv.amount_paid, inv.total, ctx)
    db.expire_all()
    pays = (await db.execute(select(Payment).where(Payment.client_id == cid))).scalars().all()
    taken = sum(p.amount for p in pays if p.kind != "refund" and p.method != "balance")
    back = sum(p.amount for p in pays if p.kind == "refund")
    assert await _accounts_total(client, h) == pytest.approx(round(taken - back, 2))


# ── B3: one sign convention ──────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_cash_summary_counts_what_clients_owe_not_their_credit(client, tenant):
    owes = await _client(client, tenant, "Owes")
    await _close(client, tenant, await _job(client, tenant, owes, 120))
    credit = await _client(client, tenant, "Credit")
    await client.post(f"/api/clients/{credit}/balance", json={"kind": "advance", "amount": 50}, headers=tenant)
    s = (await client.get("/api/cash/summary", headers=tenant)).json()
    assert s["client_debt"] == pytest.approx(120)


# ── B3: paying from client balance needs credit ──────────────────────────────
@pytest.mark.asyncio
async def test_pay_from_balance_without_credit_is_refused(client, tenant, db):
    cid = await _client(client, tenant)
    oid = await _job(client, tenant, cid, 80)
    await _close(client, tenant, oid)
    r = await client.post(f"/api/cash/orders/{oid}/pay", json={"method": "balance", "amount": 80}, headers=tenant)
    assert r.status_code == 400, r.text
    assert "credit" in r.json()["detail"].lower()
    assert await _client_balance(client, tenant, cid) == pytest.approx(80)   # Dana still owes 80
    await _reconcile(client, tenant, db, cid=cid, oid=oid)


@pytest.mark.asyncio
async def test_pay_from_balance_uses_existing_credit(client, tenant, db):
    cid = await _client(client, tenant)
    await client.post(f"/api/clients/{cid}/balance", json={"kind": "advance", "amount": 100}, headers=tenant)
    oid = await _job(client, tenant, cid, 80)
    await _close(client, tenant, oid)
    assert await _client_balance(client, tenant, cid) == pytest.approx(-20)
    r = await client.post(f"/api/cash/orders/{oid}/pay", json={"method": "balance", "amount": 80}, headers=tenant)
    assert r.status_code == 200, r.text
    assert (await _ctx(client, tenant, oid))["remaining"] == 0
    assert await _client_balance(client, tenant, cid) == pytest.approx(-20)   # 20 of credit left, not 100
    # more than the credit left is refused
    oid2 = await _job(client, tenant, cid, 50)
    await _close(client, tenant, oid2)
    r = await client.post(f"/api/cash/orders/{oid2}/pay", json={"method": "balance", "amount": 50}, headers=tenant)
    assert r.status_code == 400, r.text


@pytest.mark.asyncio
async def test_invoice_pay_from_balance_without_credit_is_refused(client, tenant):
    cid = await _client(client, tenant)
    inv = (await client.post("/api/finance/invoices", headers=tenant, json={
        "client_id": cid, "lines": [{"description": "Repair", "qty": 1, "unit_price": 80}]})).json()
    r = await client.post(f"/api/finance/invoices/{inv['id']}/pay", json={"method": "balance", "amount": 80},
                          headers=tenant)
    assert r.status_code == 400, r.text


# ── B3: deposits count toward the invoice ────────────────────────────────────
@pytest.mark.asyncio
async def test_deposit_settles_the_invoice(client, tenant, db):
    cash = await _account(client, tenant)
    cid = await _client(client, tenant)
    oid = await _job(client, tenant, cid, 100)
    r = await client.post(f"/api/cash/orders/{oid}/prepay", json={"amount": 100, "account_id": cash}, headers=tenant)
    assert r.status_code == 201, r.text
    await _close(client, tenant, oid)       # the job's invoice is created on close
    invs = await _invoices(db, oid)
    assert invs and all(i.status == "paid" for i in invs), [(i.status, i.amount_paid) for i in invs]
    await _reconcile(client, tenant, db, cid=cid, oid=oid)


@pytest.mark.asyncio
async def test_deposit_plus_final_payment_settles_the_invoice(client, tenant, db):
    cash = await _account(client, tenant)
    cid = await _client(client, tenant)
    oid = await _job(client, tenant, cid, 100)
    await client.post(f"/api/cash/orders/{oid}/prepay", json={"amount": 40, "account_id": cash}, headers=tenant)
    await _close(client, tenant, oid)
    r = await client.post(f"/api/cash/orders/{oid}/pay", json={"method": "cash", "account_id": cash}, headers=tenant)
    assert r.status_code == 200, r.text
    assert r.json()["amount"] == 60
    invs = await _invoices(db, oid)
    assert [i.status for i in invs] == ["paid"], [(i.status, i.amount_paid) for i in invs]
    await _reconcile(client, tenant, db, cid=cid, oid=oid)


@pytest.mark.asyncio
async def test_deposit_taken_after_invoice_exists_lands_on_it(client, tenant, db):
    cash = await _account(client, tenant)
    cid = await _client(client, tenant)
    oid = await _job(client, tenant, cid, 100)
    await _close(client, tenant, oid)
    await client.post(f"/api/cash/orders/{oid}/prepay", json={"amount": 100, "account_id": cash}, headers=tenant)
    assert [i.status for i in await _invoices(db, oid)] == ["paid"]
    await _reconcile(client, tenant, db, cid=cid, oid=oid)


# ── B3: the till lists sales as money in ─────────────────────────────────────
@pytest.mark.asyncio
async def test_till_counts_job_payments_as_money_in(client, tenant, db):
    cash = await _account(client, tenant)
    cid = await _client(client, tenant)
    oid = await _job(client, tenant, cid, 100)
    await _close(client, tenant, oid)
    await client.post(f"/api/cash/orders/{oid}/pay", json={"method": "cash", "account_id": cash}, headers=tenant)
    s = (await client.get(f"/api/cash/transactions?summary=true&day={date.today().isoformat()}",
                          headers=tenant)).json()["summary"]
    assert s["today_in"] == pytest.approx(100) and s["today_out"] == 0
    assert s["income"] == pytest.approx(100) and s["expense"] == 0
    await _reconcile(client, tenant, db, cid=cid, oid=oid)


# ── B3: giving change is counted once ────────────────────────────────────────
@pytest.mark.asyncio
async def test_giving_change_balances_the_drawer(client, tenant):
    loc = await _loc(client, tenant)
    cash = (await client.post("/api/cash/accounts", json={"name": "Till", "payment_option": "cash",
                                                          "location_id": loc}, headers=tenant)).json()["id"]
    sid = (await client.post("/api/finance/shifts/open", json={"location_id": loc, "opening_float": 50},
                             headers=tenant)).json()["id"]
    cid = await _client(client, tenant)
    inv = (await client.post("/api/finance/invoices", headers=tenant, json={
        "client_id": cid, "lines": [{"description": "Repair", "qty": 1, "unit_price": 80}]})).json()
    r = await client.post(f"/api/finance/invoices/{inv['id']}/pay", headers=tenant, json={
        "method": "cash", "amount": 100, "account_id": cash, "overpay": "refund"})
    assert r.status_code == 200, r.text
    # 50 float + 100 handed over − 20 change = 130 in the drawer
    r = await client.post(f"/api/finance/shifts/{sid}/close?closing_float=130", headers=tenant)
    assert r.json()["discrepancy"] == 0, r.json()
    assert r.json()["expected_cash"] == 130


# ── B3: the P&L reads discount and tax as money, not percents ────────────────
@pytest.mark.asyncio
async def test_pnl_reads_percent_discount_and_tax_correctly(client, tenant):
    r = await client.post("/api/finance/invoices", headers=tenant, json={
        "subtotal": 100, "discount": 20, "tax": 20})
    assert r.status_code == 201, r.text
    assert r.json()["total"] == pytest.approx(96)        # 100 − 20% = 80, + 20% tax = 96
    inv = (await client.get(f"/api/finance/invoices/{r.json()['id']}", headers=tenant)).json()
    assert inv["discount"] == pytest.approx(20) and inv["tax"] == pytest.approx(16)
    cash = await _account(client, tenant)
    r = await client.post(f"/api/finance/invoices/{r.json()['id']}/pay", headers=tenant,
                          json={"method": "cash", "amount": 96, "account_id": cash})
    assert r.json()["status"] == "paid", r.text
    p = (await client.get("/api/finance/pnl", headers=tenant)).json()
    assert p["revenue"] == pytest.approx(80)
    assert p["tax_collected"] == pytest.approx(16)
    assert p["gross_sales"] == pytest.approx(96)
    assert p["gross_margin_pct"] == pytest.approx(100)   # no parts: all of the 80 is gross profit


# ── B3: refunds on the reader and in cash share one cap ──────────────────────
async def _reader_paid_job(client, h):
    cash = await _account(client, h, "cash", "Cash")
    await _account(client, h, "card", "Card")
    t = await _reader(client, h)
    cid = await _client(client, h)
    oid = await _job(client, h, cid, 100)
    ch = (await _charge(client, h, t["id"], order_id=oid)).json()
    r = await client.post(f"/api/payments/terminal/charges/{ch['id']}/simulate", json={"outcome": "approve"}, headers=h)
    assert r.status_code == 200, r.text
    return cash, cid, oid, ch


@pytest.mark.asyncio
async def test_cash_refund_then_reader_refund_is_capped(client, tenant, stripe, db):
    cash, cid, oid, ch = await _reader_paid_job(client, tenant)
    r = await client.post("/api/returns/refunds", json={"order_id": oid, "amount": 100, "account_id": cash,
                                                        "reason": "unhappy"}, headers=tenant)
    assert r.status_code == 201, r.text
    r = await client.post(f"/api/payments/terminal/charges/{ch['id']}/refund", json={}, headers=tenant)
    assert r.status_code in (400, 409, 422), r.text
    assert not [p for _, p, _ in stripe.calls if p == "/v1/refunds"]


@pytest.mark.asyncio
async def test_reader_refund_then_cash_refund_is_capped(client, tenant, stripe):
    cash, cid, oid, ch = await _reader_paid_job(client, tenant)
    r = await client.post(f"/api/payments/terminal/charges/{ch['id']}/refund", json={}, headers=tenant)
    assert r.status_code == 200, r.text
    r = await client.post("/api/returns/refunds", json={"order_id": oid, "amount": 100, "account_id": cash,
                                                        "reason": "again"}, headers=tenant)
    assert r.status_code == 400, r.text


# ── B3: out-of-range discounts and prices are refused ────────────────────────
@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    {"subtotal": 100, "discount": 150},
    {"subtotal": 100, "discount": -5},
    {"subtotal": 100, "tax": -10},
    {"subtotal": -100},
    {"lines": [{"description": "x", "qty": 1, "unit_price": -50}]},
    {"lines": [{"description": "x", "qty": 0, "unit_price": 50}]},
])
async def test_invoice_rejects_out_of_range_numbers(client, tenant, body):
    r = await client.post("/api/finance/invoices", headers=tenant, json=body)
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_line_discount_can_never_make_a_job_negative(client, tenant):
    cid = await _client(client, tenant)
    oid = (await client.post("/api/orders", json={"client_id": cid}, headers=tenant)).json()["id"]
    r = await client.post(f"/api/orders/{oid}/lines", headers=tenant, json={
        "line_type": "labour", "description": "x", "qty": 1, "unit_price": 100, "discount": 250})
    assert r.status_code == 422, r.text
    r = await client.post(f"/api/orders/{oid}/lines", headers=tenant, json={
        "line_type": "labour", "description": "x", "qty": 1, "unit_price": 100,
        "discount": 250, "discount_type": "fixed"})
    assert r.status_code == 422, r.text
    lid = (await client.post(f"/api/orders/{oid}/lines", headers=tenant, json={
        "line_type": "labour", "description": "x", "qty": 1, "unit_price": 100})).json()["line_id"]
    for patch in ({"discount": 250}, {"discount": 250, "discount_type": "fixed"}, {"unit_price": -1}):
        r = await client.patch(f"/api/orders/{oid}/lines/{lid}", headers=tenant, json=patch)
        assert r.status_code in (400, 422), (patch, r.text)
    o = (await client.get(f"/api/orders/{oid}", headers=tenant)).json()
    assert o["total"] == pytest.approx(100)


# ── B3: "To collect" is net of money already taken ───────────────────────────
@pytest.mark.asyncio
async def test_to_collect_ignores_money_already_taken(client, tenant):
    cash = await _account(client, tenant)
    cid = await _client(client, tenant)
    oid = await _job(client, tenant, cid, 100)
    await client.post(f"/api/cash/orders/{oid}/prepay", json={"amount": 30, "account_id": cash}, headers=tenant)
    await _job(client, tenant, cid, 50)
    d = (await client.get("/api/reports/company-dashboard", headers=tenant)).json()
    assert d["kpis"]["receivables"] == pytest.approx(120)    # (100 − 30) + 50


# ── B3: a pay link can't dodge the balance cap with another currency ─────────
@pytest.mark.asyncio
async def test_checkout_in_another_currency_is_refused(client, tenant, db):
    cid = await _client(client, tenant)
    oid = await _job(client, tenant, cid, 100)
    from backend.models import Organisation
    org_id = (await db.execute(select(Order.org_id).where(Order.id == oid))).scalar_one()
    ccy = (await db.execute(select(Organisation.currency_code).where(Organisation.id == org_id))).scalar_one()
    other = "jpy" if (ccy or "").lower() != "jpy" else "usd"
    r = await client.post("/api/payments/checkout", json={"order_id": oid, "amount": 100, "currency": other},
                          headers=tenant)
    assert r.status_code == 422, r.text
    r = await client.post("/api/payments/checkout", json={"order_id": oid, "amount": 150, "currency": ccy},
                          headers=tenant)
    assert r.status_code == 422, r.text
    r = await client.post("/api/payments/checkout", json={"order_id": oid, "amount": 100, "currency": ccy.upper()},
                          headers=tenant)
    assert r.status_code == 201, r.text


# ── B7: never the operator's Stripe/Square/SumUp account ─────────────────────
@pytest.mark.asyncio
async def test_shop_without_keys_never_uses_operator_keys(client, tenant, monkeypatch):
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_operator", raising=False)
    monkeypatch.setattr(settings, "SQUARE_ACCESS_TOKEN", "sq-operator", raising=False)
    monkeypatch.setattr(settings, "SQUARE_LOCATION_ID", "L1", raising=False)
    monkeypatch.setattr(settings, "SUMUP_API_KEY", "sup_operator", raising=False)
    monkeypatch.setattr(settings, "SUMUP_MERCHANT_CODE", "M1", raising=False)
    calls = []

    def _no_network(req):
        calls.append(str(req.url))
        return httpx.Response(500)
    real = httpx.AsyncClient

    def _client_factory(*a, **kw):
        kw["transport"] = httpx.MockTransport(_no_network)
        return real(*a, **kw)
    monkeypatch.setattr(httpx, "AsyncClient", _client_factory)

    cfg = (await client.get("/api/payments/config", headers=tenant)).json()
    assert cfg["available"]["stripe"] is False and cfg["available"]["square"] is False
    assert cfg["available"]["sumup"] is False
    cid = await _client(client, tenant)
    oid = await _job(client, tenant, cid, 100)
    r = await client.post("/api/payments/checkout", json={"order_id": oid, "amount": 100}, headers=tenant)
    assert r.status_code == 201, r.text
    assert r.json()["provider"] == "simulated"
    assert not calls


@pytest.mark.asyncio
async def test_payment_settings_show_the_webhook_urls(client, tenant):
    cfg = (await client.get("/api/payments/config", headers=tenant)).json()
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    org_id = me.get("org_id") or me["org"]["id"]
    assert cfg["webhook_urls"]["stripe"].endswith(f"/api/payments/stripe/webhook/{org_id}")
    assert cfg["webhook_urls"]["square"].endswith(f"/api/payments/square/webhook/{org_id}")


# ── B7: a paid Stripe link flips to paid when the pay page polls ─────────────
@pytest.mark.asyncio
async def test_stripe_link_flips_to_paid_on_poll(client, tenant, db, monkeypatch):
    from backend.tests.test_opf_card_readers import _save_creds
    await _save_creds(client, tenant, "stripe", {"secret_key": "sk_test_shop"})
    cash = await _account(client, tenant, "card", "Card")
    sessions = {}

    def _stripe_api(req):
        if req.url.path == "/v1/checkout/sessions" and req.method == "POST":
            form = dict(httpx.QueryParams(req.content.decode()))
            sessions["cs_1"] = {"id": "cs_1", "url": "https://checkout.stripe.test/cs_1", "payment_status": "unpaid",
                                "amount_total": 10000, "currency": form["line_items[0][price_data][currency]"]}
            return httpx.Response(200, json=sessions["cs_1"])
        if req.url.path == "/v1/checkout/sessions/cs_1":
            assert req.headers["authorization"].startswith("Basic ")
            return httpx.Response(200, json=sessions["cs_1"])
        return httpx.Response(404, json={})
    real = httpx.AsyncClient

    def _client_factory(*a, **kw):
        kw["transport"] = httpx.MockTransport(_stripe_api)
        return real(*a, **kw)
    monkeypatch.setattr(httpx, "AsyncClient", _client_factory)

    cid = await _client(client, tenant)
    oid = await _job(client, tenant, cid, 100)
    await _close(client, tenant, oid)
    i = (await client.post("/api/payments/checkout", json={"order_id": oid, "amount": 100, "account_id": cash},
                           headers=tenant)).json()
    assert i["provider"] == "stripe"
    r = await client.post(f"/api/payments/intents/{i['id']}/refresh")
    assert r.json()["status"] != "succeeded"
    sessions["cs_1"]["payment_status"] = "paid"
    from backend.services import ratelimit
    monkeypatch.setattr(ratelimit, "aenforce", _noop_limit)
    r = await client.post(f"/api/payments/intents/{i['id']}/refresh")
    assert r.json()["status"] == "succeeded", r.json()
    assert (await _ctx(client, tenant, oid))["remaining"] == 0
    await _reconcile(client, tenant, db, cid=cid, oid=oid)


@pytest.mark.asyncio
async def test_stripe_poll_ignores_a_session_for_another_amount(client, tenant, monkeypatch):
    from backend.tests.test_opf_card_readers import _save_creds
    await _save_creds(client, tenant, "stripe", {"secret_key": "sk_test_shop"})

    seen = {}

    def _stripe_api(req):
        if req.method == "POST":
            seen["ccy"] = dict(httpx.QueryParams(req.content.decode()))["line_items[0][price_data][currency]"]
        body = {"id": "cs_2", "url": "https://checkout.stripe.test/cs_2", "payment_status": "paid",
                "amount_total": 100, "currency": seen["ccy"]}      # 1.00, not 100.00
        return httpx.Response(200, json=body)
    real = httpx.AsyncClient

    def _client_factory(*a, **kw):
        kw["transport"] = httpx.MockTransport(_stripe_api)
        return real(*a, **kw)
    monkeypatch.setattr(httpx, "AsyncClient", _client_factory)
    cid = await _client(client, tenant)
    oid = await _job(client, tenant, cid, 100)
    i = (await client.post("/api/payments/checkout", json={"order_id": oid, "amount": 100}, headers=tenant)).json()
    r = await client.post(f"/api/payments/intents/{i['id']}/refresh")
    assert r.json()["status"] != "succeeded"


async def _noop_limit(*a, **kw):
    return None


# ── Dropped jobs: the write-off lowers the job's ledger charge ───────────────
@pytest.mark.asyncio
async def test_dropped_part_paid_job_reconciles(client, tenant, db):
    cash = await _account(client, tenant)
    cid = await _client(client, tenant)
    oid = await _job(client, tenant, cid, 100)
    await client.post(f"/api/cash/orders/{oid}/prepay", json={"amount": 30, "account_id": cash}, headers=tenant)
    statuses = (await client.get("/api/statuses?record_type=work_order", headers=tenant)).json()
    items = statuses.get("items", statuses) if isinstance(statuses, dict) else statuses
    dropped = next((s["key"] for s in items if s.get("status_group") == "dropped"), None)
    if not dropped:
        pytest.skip("no dropped status seeded")
    r = await client.patch(f"/api/orders/{oid}/status", json={"status": dropped, "reason": "Customer changed their mind"}, headers=tenant)
    assert r.status_code == 200, r.text
    db.expire_all()
    o = (await db.execute(select(Order).where(Order.id == oid))).scalar_one()
    assert float(o.written_off) == pytest.approx(70)
    assert await _client_balance(client, tenant, cid) == pytest.approx(0)
    assert (await _ctx(client, tenant, oid))["remaining"] == 0


def test_unused_sync_write_off_is_gone():
    from backend.services import order_money
    assert not hasattr(order_money, "sync_write_off")
