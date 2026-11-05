"""Hardening council (High): a waiting reader charge plus an unreachable provider locked the job
to card.

services/terminal.cancel() re-raised the provider error and the charge stayed "pending" with no
expiry; order_money.reader_charge_in_flight() then refused every cash/other payment on the order
("A card reader charge ... still in progress"), so the till could not take cash during a Stripe
outage or WAN flap. Fix: a cancel the provider couldn't confirm is noted on the charge (it stays
pending so a later "succeeded" webhook/poll still posts the money), and such a charge no longer
blocks money coming in. "processing" (approved, not yet posted) still blocks, and so does a waiting
charge nobody tried to cancel, however old: the reader may still show the amount, and cash on top
would be a silent double charge. Archiving the job and crediting its invoice stay strict (any open
charge blocks): a late card success must not land on a deleted job or a credited invoice.
"""
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from backend.models import TerminalCharge
from backend.services import terminal as svc
from backend.services.dates import now_utc
from backend.tests.test_opf_card_readers import (  # noqa: F401
    _card_account,
    _charge,
    _order,
    _owed,
    _payments,
    _reader,
    _stripe_event,
    stripe,
)

pytestmark = pytest.mark.asyncio


def _dead(req: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("Stripe unreachable", request=req)


async def _cash_account(client, h):
    return (await client.post("/api/cash/accounts", json={"name": "Till", "payment_option": "cash"},
                              headers=h)).json()["id"]


async def _pay_cash(client, h, oid, till, amount=100):
    return await client.post(f"/api/cash/orders/{oid}/pay", headers=h,
                             json={"method": "cash", "account_id": till, "amount": amount})


async def _setup(client, tenant):
    await _card_account(client, tenant)
    till = await _cash_account(client, tenant)
    t = await _reader(client, tenant)
    oid = await _order(client, tenant, 100)
    ch = (await _charge(client, tenant, t["id"], order_id=oid)).json()
    assert ch["status"] == "pending", ch
    return till, t, oid, ch


async def test_stripe_outage_does_not_lock_the_order_to_card(client, tenant, stripe, monkeypatch):  # noqa: F811
    till, t, oid, ch = await _setup(client, tenant)
    monkeypatch.setattr(svc, "_TRANSPORT", httpx.MockTransport(_dead))   # the internet goes away

    r = await client.post(f"/api/payments/terminal/charges/{ch['id']}/cancel", headers=tenant)
    assert r.status_code == 502, r.text          # staff see the outage, honestly
    r2 = await client.post(f"/api/payments/terminal/readers/{t['id']}/clear", headers=tenant)
    assert r2.status_code == 502, r2.text
    got = (await client.get(f"/api/payments/terminal/charges/{ch['id']}", headers=tenant)).json()
    assert got["status"] == "pending"            # unknown outcome: never claimed "canceled"
    assert got["error"], got                     # ...but the cancel attempt is on record

    # The customer gives up on the card and pays cash.
    pay = await _pay_cash(client, tenant, oid, till)
    assert pay.status_code == 200, pay.text


async def test_card_taken_after_cash_is_still_posted(client, tenant, stripe, monkeypatch, db):  # noqa: F811
    """Money safety: the charge stays live. If the reader did take the card after all, the webhook
    posts it (the order is then overpaid, which the usual overpayment flow handles)."""
    till, t, oid, ch = await _setup(client, tenant)
    monkeypatch.setattr(svc, "_TRANSPORT", httpx.MockTransport(_dead))
    assert (await client.post(f"/api/payments/terminal/charges/{ch['id']}/cancel", headers=tenant)).status_code == 502
    assert (await _pay_cash(client, tenant, oid, till)).status_code == 200

    monkeypatch.setattr(svc, "_TRANSPORT", httpx.MockTransport(stripe))   # Stripe is back
    pi = dict(stripe.pi[ch["provider_ref"]], status="succeeded", amount_received=10000)
    r = await _stripe_event(client, "payment_intent.succeeded", pi)
    assert r.status_code == 200 and r.json()["status"] == "succeeded", r.text
    pays = await _payments(db, oid)
    assert sorted((p.method, float(p.amount)) for p in pays) == [("card", 100.0), ("cash", 100.0)]
    assert await _owed(db, oid) == 0      # balance_due never goes negative; the order is overpaid by 100


async def test_fresh_pending_charge_still_blocks_other_money(client, tenant, stripe):  # noqa: F811
    till, t, oid, ch = await _setup(client, tenant)
    pay = await _pay_cash(client, tenant, oid, till)
    assert pay.status_code == 409, pay.text


async def test_old_pending_charge_with_no_cancel_attempt_still_blocks_cash(client, tenant, stripe, db):  # noqa: F811
    """Review: age alone never unblocks. With no cancel attempt the reader may still be showing
    the amount, so cash on top of it would be a double charge nobody flagged."""
    till, t, oid, ch = await _setup(client, tenant)
    row = (await db.execute(select(TerminalCharge).where(TerminalCharge.id == ch["id"]))).scalar_one()
    row.created_at = now_utc() - timedelta(minutes=16)
    await db.commit()
    assert (await _pay_cash(client, tenant, oid, till, 40)).status_code == 409

    row.status = "processing"      # approved on the reader, money not yet posted: must still block
    await db.commit()
    assert (await _pay_cash(client, tenant, oid, till, 10)).status_code == 409


async def test_unconfirmed_cancel_is_saved_even_though_the_request_fails(client, tenant, stripe, monkeypatch, db):  # noqa: F811
    """The service only notes the attempt; the route commits before answering 502."""
    till, t, oid, ch = await _setup(client, tenant)
    monkeypatch.setattr(svc, "_TRANSPORT", httpx.MockTransport(_dead))
    assert (await client.post(f"/api/payments/terminal/charges/{ch['id']}/cancel", headers=tenant)).status_code == 502
    db.expire_all()
    row = (await db.execute(select(TerminalCharge).where(TerminalCharge.id == ch["id"]))).scalar_one()
    assert row.status == "pending"
    assert row.error and row.error.startswith("Cancel requested, but not confirmed"), row.error


# ── Review: archive and credit notes stay strict ────────────────────────────────────────────────

async def test_unconfirmed_cancel_does_not_let_the_job_be_archived(client, tenant, stripe, monkeypatch):  # noqa: F811
    """A late card success on an archived job would post money onto a deleted job."""
    till, t, oid, ch = await _setup(client, tenant)
    monkeypatch.setattr(svc, "_TRANSPORT", httpx.MockTransport(_dead))
    assert (await client.post(f"/api/payments/terminal/charges/{ch['id']}/cancel", headers=tenant)).status_code == 502
    assert (await _pay_cash(client, tenant, oid, till)).status_code == 200    # cash is fine...
    r = await client.post(f"/api/orders/{oid}/archive", json={"reason": "x", "refund": True}, headers=tenant)
    assert r.status_code == 409, r.text                                       # ...archiving is not
    assert "card reader" in r.json()["detail"]


async def test_unconfirmed_cancel_does_not_let_the_invoice_be_credited(client, tenant, stripe, monkeypatch):  # noqa: F811
    await _card_account(client, tenant)
    t = await _reader(client, tenant)
    oid = await _order(client, tenant, 100)
    inv = await client.post(f"/api/cash/orders/{oid}/invoices", json={}, headers=tenant)
    assert inv.status_code in (200, 201), inv.text
    ch = (await _charge(client, tenant, t["id"], order_id=oid)).json()
    assert ch["status"] == "pending", ch
    monkeypatch.setattr(svc, "_TRANSPORT", httpx.MockTransport(_dead))
    assert (await client.post(f"/api/payments/terminal/charges/{ch['id']}/cancel", headers=tenant)).status_code == 502

    cn = await client.post("/api/finance/credit-notes", headers=tenant, json={
        "invoice_id": inv.json()["id"], "reason": "Goodwill",
        "lines": [{"description": "Refund part", "qty": 1, "unit_price": 30}]})
    assert cn.status_code == 201, cn.text
    r = await client.post(f"/api/finance/credit-notes/{cn.json()['id']}/issue", headers=tenant)
    assert r.status_code == 409, r.text
    assert "card reader" in r.json()["detail"]
