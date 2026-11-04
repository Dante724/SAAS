"""Hardening council (High): a waiting reader charge plus an unreachable provider locked the job
to card.

services/terminal.cancel() re-raised the provider error and the charge stayed "pending" with no
expiry; order_money.reader_charge_in_flight() then refused every cash/other payment on the order
("A card reader charge ... still in progress"), so the till could not take cash during a Stripe
outage or WAN flap. Fix: a cancel the provider couldn't confirm is noted on the charge (it stays
pending so a later "succeeded" webhook/poll still posts the money), and such a charge — or a
pending one older than READER_PENDING_MAX_MIN — no longer blocks other payments. "processing"
(approved, not yet posted) still blocks.
"""
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from backend.models import TerminalCharge
from backend.services import terminal as svc
from backend.services.dates import now_utc
from backend.services.order_money import READER_PENDING_MAX_MIN
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


async def test_stale_pending_charge_stops_blocking_but_processing_never_does(client, tenant, stripe, db):  # noqa: F811
    till, t, oid, ch = await _setup(client, tenant)
    row = (await db.execute(select(TerminalCharge).where(TerminalCharge.id == ch["id"]))).scalar_one()
    row.created_at = now_utc() - timedelta(minutes=READER_PENDING_MAX_MIN + 1)
    await db.commit()
    assert (await _pay_cash(client, tenant, oid, till, 40)).status_code == 200

    row.status = "processing"      # approved on the reader, money not yet posted: must still block
    await db.commit()
    assert (await _pay_cash(client, tenant, oid, till, 10)).status_code == 409
