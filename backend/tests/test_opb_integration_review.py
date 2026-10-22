"""Integration review (roadmap/orderry-parity) — team B findings M1–M9, P2, P6.

Each test reproduces the reviewer's scenario and fails on the code before the fix.
"""
import uuid

import pytest
from sqlalchemy import select

from backend.tests.test_money_hardening import _account, _order_with_total
from backend.tests.test_opb_billing import _register, _teammate

pytestmark = pytest.mark.asyncio


async def _order_invoice(client, H, oid):
    r = await client.post("/api/finance/invoices", headers=H, json={"order_id": oid})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _credit(client, H, iid, amount):
    r = await client.post("/api/finance/credit-notes", headers=H, json={
        "invoice_id": iid, "lines": [{"description": "Credit", "qty": 1, "unit_price": amount}]})
    assert r.status_code == 201, r.text
    cn = r.json()
    r = await client.post(f"/api/finance/credit-notes/{cn['id']}/issue", headers=H)
    assert r.status_code == 200, r.text
    return cn["id"]


async def _pay(client, H, oid, aid, amount):
    return await client.post(f"/api/cash/orders/{oid}/pay", headers=H,
                             json={"method": "cash", "account_id": aid, "amount": amount})


async def _org_id(client, H):
    return (await client.get("/api/auth/me", headers=H)).json()["org_id"]


async def _reader_charge(db, org_id, oid, *, status, amount=0.0, refunded=0.0):
    from backend.models import PaymentTerminal, TerminalCharge
    t = PaymentTerminal(org_id=org_id, provider="simulated", reader_id=f"sim-{uuid.uuid4().hex[:6]}", label="Reader")
    db.add(t)
    await db.flush()
    db.add(TerminalCharge(org_id=org_id, terminal_id=t.id, provider="simulated", order_id=oid,
                          amount=amount, refunded_amount=refunded, status=status))
    await db.commit()


# ── M1: the order pay screen and endpoint honour credit notes ────────────────
async def test_m1_order_payment_capped_at_balance_after_credit(client):
    H = await _register(client)
    _cid, oid = await _order_with_total(client, H, 100)
    aid = await _account(client, H)
    await _credit(client, H, await _order_invoice(client, H, oid), 30)
    ctx = (await client.get(f"/api/cash/orders/{oid}/payment-context", headers=H)).json()
    assert ctx["remaining"] == 70 and ctx["credited"] == 30
    r = await _pay(client, H, oid, aid, 100)
    assert r.status_code == 400 and "balance due" in r.text      # 130 covered on a 100 order before
    assert (await _pay(client, H, oid, aid, 70)).status_code == 200


# ── M2: archive can't leave phantom credit from issued credit notes ──────────
async def test_m2_archive_blocked_until_credit_notes_canceled(client):
    H = await _register(client)
    cid, oid = await _order_with_total(client, H, 100)
    aid = await _account(client, H)
    iid = await _order_invoice(client, H, oid)
    cn = await _credit(client, H, iid, 30)
    assert (await _pay(client, H, oid, aid, 70)).status_code == 200
    r = await client.delete(f"/api/orders/{oid}?refund=true", headers=H)
    assert r.status_code == 409 and "credit note" in r.text
    await client.post(f"/api/finance/credit-notes/{cn}/cancel", headers=H)
    r = await client.delete(f"/api/orders/{oid}?refund=true", headers=H)
    assert r.status_code in (200, 201), r.text
    bal = (await client.get(f"/api/clients/{cid}/balance", headers=H)).json()["balance"]
    assert round(float(bal), 2) == 0.0, bal                       # was −30 phantom credit


# ── M3: archive-with-refund refuses money taken on a card reader ─────────────
async def test_m3_archive_refund_refuses_reader_money(client, db):
    H = await _register(client)
    _cid, oid = await _order_with_total(client, H, 100)
    aid = await _account(client, H)
    assert (await _pay(client, H, oid, aid, 100)).status_code == 200
    await _reader_charge(db, await _org_id(client, H), oid, status="succeeded", amount=100)
    r = await client.delete(f"/api/orders/{oid}?refund=true", headers=H)
    assert r.status_code == 409 and "card reader" in r.text


async def test_m3_m5_reader_in_flight_blocks_archive(client, db):
    H = await _register(client)
    _cid, oid = await _order_with_total(client, H, 100)
    await _reader_charge(db, await _org_id(client, H), oid, status="pending", amount=100)
    r = await client.delete(f"/api/orders/{oid}?refund=true", headers=H)
    assert r.status_code == 409 and "in progress" in r.text


# ── M4a: issuing a credit note revokes pay links for the old amount ──────────
async def test_m4a_issue_revokes_oversized_pay_link_and_waits_for_reader(client, db):
    from backend.models import PaymentIntent
    H = await _register(client)
    _cid, oid = await _order_with_total(client, H, 100)
    org = await _org_id(client, H)
    iid = await _order_invoice(client, H, oid)
    big = PaymentIntent(org_id=org, order_id=oid, provider="simulated", amount=100, status="created")
    small = PaymentIntent(org_id=org, order_id=oid, provider="simulated", amount=50, status="created")
    db.add_all([big, small])
    await db.commit()
    await _credit(client, H, iid, 30)
    db.expire_all()
    statuses = {i.amount: i.status for i in (await db.execute(
        select(PaymentIntent).where(PaymentIntent.order_id == oid))).scalars()}
    assert statuses == {100: "canceled", 50: "created"}           # the 100 link would overcharge

    await _reader_charge(db, org, oid, status="pending", amount=70)
    r = await client.post("/api/finance/credit-notes", headers=H, json={
        "invoice_id": iid, "lines": [{"description": "More", "qty": 1, "unit_price": 5}]})
    r = await client.post(f"/api/finance/credit-notes/{r.json()['id']}/issue", headers=H)
    assert r.status_code == 409 and "reader" in r.text


# ── M5: no cash while a reader charge is pending ─────────────────────────────
async def test_m5_pay_refused_while_reader_charge_pending(client, db):
    H = await _register(client)
    _cid, oid = await _order_with_total(client, H, 100)
    aid = await _account(client, H)
    iid = await _order_invoice(client, H, oid)
    await _reader_charge(db, await _org_id(client, H), oid, status="pending", amount=100)
    assert (await _pay(client, H, oid, aid, 100)).status_code == 409
    r = await client.post(f"/api/finance/invoices/{iid}/pay", headers=H,
                          json={"amount": 100, "method": "cash", "account_id": aid})
    assert r.status_code == 409


# ── M7: track page, {balance_due} and the require-payment gate net out credits ─
async def test_m7_balance_everywhere_nets_out_credit(client, db):
    from backend.models import Order, OrderStatus
    from backend.routers.public import _paid_fields
    from backend.services.message_vars import order_values
    H = await _register(client)
    _cid, oid = await _order_with_total(client, H, 100)
    await _credit(client, H, await _order_invoice(client, H, oid), 100)
    order = (await db.execute(select(Order).where(Order.id == oid))).scalar_one()
    fields = await _paid_fields(db, order, 1)
    assert fields["balance_due"] == 0 and fields["paid_in_full"] is True
    vals = await order_values(db, order.org_id, order, "Due {balance_due}")
    assert "100" not in vals["balance_due"]
    # a status that requires full payment accepts a fully credited order
    st = (await db.execute(select(OrderStatus).where(
        OrderStatus.org_id == order.org_id, OrderStatus.key == "done"))).scalars().first()
    st.require_payment = True
    await db.commit()
    r = await client.patch(f"/api/orders/{oid}/status", json={"status": "done"}, headers=H)
    assert r.status_code == 200, r.text


# ── M8: credit isn't double counted after attaching a second order ───────────
async def test_m8_credit_split_once_after_attach(client, db):
    from backend.models import InvoiceOrder
    from backend.services.order_money import credited
    H = await _register(client)
    _c1, a = await _order_with_total(client, H, 100)
    _c2, b = await _order_with_total(client, H, 100)
    aid = await _account(client, H)
    assert (await _pay(client, H, a, aid, 10)).status_code == 200      # auto-creates A's invoice
    iid = (await db.execute(select(InvoiceOrder.invoice_id).where(InvoiceOrder.order_id == a))).scalar_one()
    assert (await client.post(f"/api/finance/invoices/{iid}/orders", json={"order_id": b}, headers=H)).status_code == 200
    await _credit(client, H, iid, 40)
    assert round(await credited(db, a) + await credited(db, b), 2) == 40


# ── M9: revenue excludes credited amounts ────────────────────────────────────
async def test_m9_revenue_net_of_credit(client):
    H = await _register(client)
    cid = (await client.post("/api/clients", json={"name": "R"}, headers=H)).json()["id"]
    iid = (await client.post("/api/finance/invoices", headers=H, json={
        "client_id": cid, "lines": [{"description": "Fix", "qty": 1, "unit_price": 100}]})).json()["id"]
    await client.post(f"/api/finance/invoices/{iid}/pay", headers=H, json={"amount": 80, "method": "cash"})
    await _credit(client, H, iid, 20)
    s = (await client.get("/api/finance/reports/summary", headers=H)).json()
    assert s["total_revenue"] == 80


# ── P2: who may issue credit notes ───────────────────────────────────────────
def _migration():
    import importlib.util
    from pathlib import Path
    p = Path(__file__).resolve().parents[1] / "migrations" / "versions" / "opb1_credit_notes.py"
    spec = importlib.util.spec_from_file_location("opb1", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def test_p2_migration_grants_issue_only_with_money_codes():
    m = _migration()
    tech = {"can_view_invoices": True, "can_create_invoices": True, "can_edit_issued_invoices": True,
            "can_view_finance": False}
    assert "can_issue_credit_notes" not in m.grants_for(tech, True)
    assert m.grants_for(tech, True)["can_view_credit_notes"] is True
    manager = {**tech, "can_view_finance": True, "can_create_returns": True}
    assert m.grants_for(manager, True)["can_issue_credit_notes"] is True
    # legacy default-allow roles: a technician-style role gets an explicit deny
    assert m.grants_for({"can_view_finance": False}, False) == {"can_issue_credit_notes": False}
    assert m.grants_for({}, False) == {}
    assert m.grants_for({"all": "write"}, False) == {}


async def test_p2_standalone_note_needs_client_payments_permission(client):
    H = await _register(client)
    cid = (await client.post("/api/clients", json={"name": "S"}, headers=H)).json()["id"]
    T = await _teammate(client, H, "can_make_client_payments")
    r = await client.post("/api/finance/credit-notes", headers=T, json={
        "client_id": cid, "lines": [{"description": "Goodwill", "qty": 1, "unit_price": 500}]})
    assert r.status_code == 201
    assert (await client.post(f"/api/finance/credit-notes/{r.json()['id']}/issue", headers=T)).status_code == 403
    assert (await client.post(f"/api/finance/credit-notes/{r.json()['id']}/issue", headers=H)).status_code == 200


# ── P6: template previews don't leak real invoices / credit notes ────────────
async def test_p6_preview_needs_document_permission(client):
    H = await _register(client)
    cid = (await client.post("/api/clients", json={"name": "Secret Client"}, headers=H)).json()["id"]
    await client.post("/api/finance/credit-notes", headers=H, json={
        "client_id": cid, "lines": [{"description": "x", "qty": 1, "unit_price": 1}]})
    T = await _teammate(client, H, "can_view_credit_notes", "can_view_invoices")
    for path in ("/api/documents/preview", "/api/documents/print-view"):
        for dt in ("credit_note", "invoice"):
            r = await client.post(path, headers=T, json={"body_html": "{Client Name}", "doc_type": dt})
            assert r.status_code == 403, (path, dt, r.text)
    r = await client.post("/api/documents/preview", headers=H, json={"body_html": "{Client Name}", "doc_type": "credit_note"})
    assert r.status_code == 200 and "Secret Client" in r.json()["html"]
