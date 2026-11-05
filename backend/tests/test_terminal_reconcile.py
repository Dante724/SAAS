"""Follow-up to #99 (reviewer and cash-review findings), card readers and Idempotency-Key takeover.

1. Square: the pending charge row is committed before Square is called (as on Stripe). A request
   stalled inside the Square call for over 2 minutes has therefore committed, so a retry with its
   key can't take it over and no second checkout is sent.
2. Takeover staleness is measured on the database clock, not the app server's.
3. A Square charge whose create call never answered (no checkout id) is found by the poll through
   a checkout search on reference_id, so it settles without the webhook.
4. A superseded charge canceled at the provider is settled as canceled, so the sweep stops
   canceling it every 10 minutes.
5. The sweep also cancels pending charges the reader never confirmed, and pending charges gone
   stale, so a reader isn't locked by the one-open-charge index until someone cancels by hand.
"""
import asyncio
import json
import uuid
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import func, select, update

from backend.config import settings
from backend.models import IdempotencyRecord, Payment, TerminalCharge, Transaction
from backend.services import terminal as svc
from backend.services.dates import now_utc
from backend.tests.test_opf_card_readers import SQ_OK_DEVICE, FakeSquare, FakeStripe


# ── fakes ────────────────────────────────────────────────────────────────────
class SlowSquare(FakeSquare):
    """FakeSquare plus: checkout search, a stall on the first create, and a create whose answer
    is lost after the checkout was made."""

    def __init__(self):
        super().__init__()
        self.stall: asyncio.Event | None = None
        self.started = asyncio.Event()
        self.lose_create_answer = False

    async def __call__(self, req):
        path = req.url.path
        if path == "/v2/terminals/checkouts/search":
            body = json.loads(req.content or b"{}")
            self.calls.append((req.method, path, body))
            dev = (((body.get("query") or {}).get("filter") or {}).get("device_id"))
            return httpx.Response(200, json={"checkouts": [
                c for c in self.checkouts.values() if not dev or (c.get("device_options") or {}).get("device_id") == dev]})
        if path == "/v2/terminals/checkouts" and req.method == "POST":
            if self.stall is not None and not self.started.is_set():
                self.started.set()
                await asyncio.wait_for(self.stall.wait(), 20)
            res = FakeSquare.__call__(self, req)
            if self.lose_create_answer:
                return httpx.Response(500, json={"errors": [{"detail": "upstream timeout"}]})
            return res
        return FakeSquare.__call__(self, req)

    def creates(self):
        return sum(1 for m, p, _ in self.calls if m == "POST" and p == "/v2/terminals/checkouts")


class FlakyStripe(FakeStripe):
    fail_process = False
    fail_cancel = False

    def __call__(self, req):
        p = req.url.path
        if self.fail_process and p.endswith("/process_payment_intent"):
            self.calls.append((req.method, p, {}))
            return httpx.Response(500, json={"error": {"message": "upstream timeout"}})
        if self.fail_cancel and p.startswith("/v1/payment_intents/") and p.endswith("/cancel"):
            self.calls.append((req.method, p, {}))
            return httpx.Response(500, json={"error": {"message": "upstream timeout"}})
        return super().__call__(req)

    def cancels(self):
        return [p for m, p, _ in self.calls if m == "POST" and p.startswith("/v1/payment_intents/") and p.endswith("/cancel")]


async def _creds(client, h, provider, data):
    r = await client.put("/api/integrations/credentials", json={"provider": provider, "data": data}, headers=h)
    assert r.status_code == 200, r.text


@pytest.fixture
def slow_square(monkeypatch):
    fake = SlowSquare()
    monkeypatch.setattr(settings, "SQUARE_ENV", "sandbox", raising=False)
    monkeypatch.setattr(settings, "SQUARE_WEBHOOK_SIGNATURE_KEY", "sq-sig-key", raising=False)
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://shop.example", raising=False)
    monkeypatch.setattr(svc, "_TRANSPORT", httpx.MockTransport(fake))
    return fake


@pytest.fixture
def flaky_stripe(monkeypatch):
    fake = FlakyStripe()
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", "whsec_fake", raising=False)
    monkeypatch.setattr(svc, "_TRANSPORT", httpx.MockTransport(fake))
    return fake


async def _square_reader(client, h):
    await _creds(client, h, "square", {"access_token": "sq-sandbox-token", "env": "sandbox",
                                       "webhook_signature_key": "sq-sig-key"})
    r = await client.post("/api/payments/terminal/readers", json={
        "provider": "square", "label": "Counter", "device_id": SQ_OK_DEVICE}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _stripe_reader(client, h):
    await _creds(client, h, "stripe", {"secret_key": "sk_test_fake", "webhook_secret": "whsec_fake"})
    r = await client.post("/api/payments/terminal/readers", json={
        "provider": "stripe", "label": "Front desk",
        "address": {"line1": "1 Main St", "city": "Austin", "state": "TX", "postal_code": "78701", "country": "US"}},
        headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _order(client, h, total=100):
    cid = (await client.post("/api/clients", json={"name": "C", "phone": "+1"}, headers=h)).json()["id"]
    oid = (await client.post("/api/orders", json={"client_id": cid}, headers=h)).json()["id"]
    r = await client.post(f"/api/orders/{oid}/lines", json={
        "line_type": "labour", "description": "Fix", "qty": 1, "unit_price": total}, headers=h)
    assert r.status_code in (200, 201), r.text
    return oid


async def _till(client, h):
    r = await client.post("/api/cash/accounts", json={"name": "Till", "payment_option": "cash"}, headers=h)
    return r.json()["id"]


async def _charge_row(db, cid):
    db.expire_all()
    return (await db.execute(select(TerminalCharge).where(TerminalCharge.id == cid))).scalar_one()


async def _age_charge(db, cid, minutes):
    await db.execute(update(TerminalCharge).where(TerminalCharge.id == cid).values(
        created_at=now_utc().replace(tzinfo=None) - timedelta(minutes=minutes)))
    await db.commit()


# ── 1. Square stall: commit first, no takeover, no second checkout ───────────
@pytest.mark.asyncio
async def test_square_call_stalled_past_two_minutes_is_not_taken_over(client, tenant, db, slow_square):
    tid = await _square_reader(client, tenant)
    oid = await _order(client, tenant)
    key = f"k-{uuid.uuid4().hex}"
    hk = {**tenant, "Idempotency-Key": key}
    slow_square.stall = asyncio.Event()
    body = {"terminal_id": tid, "order_id": oid}
    first = asyncio.ensure_future(client.post("/api/payments/terminal/charge", json=body, headers=hk))
    await asyncio.wait_for(slow_square.started.wait(), 10)
    # The original has now been inside the Square call for "3 minutes".
    await db.execute(update(IdempotencyRecord).where(IdempotencyRecord.key == key).values(
        created_at=now_utc().replace(tzinfo=None) - timedelta(minutes=3)))
    await db.commit()
    asyncio.get_running_loop().call_later(2, slow_square.stall.set)   # unblocks a pre-fix deadlock
    retry = await asyncio.wait_for(client.post("/api/payments/terminal/charge", json=body, headers=hk), 30)
    slow_square.stall.set()
    done = await asyncio.wait_for(first, 30)
    assert retry.status_code == 409, retry.text
    assert done.status_code == 201, done.text
    assert slow_square.creates() == 1                                   # one checkout on the device
    assert len(slow_square.checkouts) == 1
    db.expire_all()
    assert (await db.execute(select(func.count()).select_from(TerminalCharge).where(
        TerminalCharge.order_id == oid))).scalar_one() == 1
    again = await client.post("/api/payments/terminal/charge", json=body, headers=hk)
    assert again.status_code == 201 and again.json()["id"] == done.json()["id"]


# ── 2. takeover staleness uses the database clock ─────────────────────────────
@pytest.mark.asyncio
async def test_takeover_staleness_uses_the_database_clock(client, tenant, db, monkeypatch):
    """The app server's clock runs 10 minutes fast. A claim made 30 seconds ago (database time)
    is still in flight and must not be taken over."""
    from datetime import datetime as real_dt

    from backend.services import idempotency as idem
    aid = await _till(client, tenant)
    from backend.models import Account
    org = (await db.execute(select(Account.org_id).where(Account.id == aid))).scalar_one()
    key = f"k-{uuid.uuid4().hex}"
    db.add(IdempotencyRecord(org_id=org, key=key, endpoint="cash.transaction",
                             created_at=real_dt.utcnow() - timedelta(seconds=30)))
    await db.commit()

    class FastClock(real_dt):
        @classmethod
        def utcnow(cls):
            return real_dt.utcnow() + timedelta(minutes=10)
    monkeypatch.setattr(idem, "datetime", FastClock)
    body = {"account_id": aid, "txn_type": "income", "amount": 25, "comment": "idem-clock"}
    r = await client.post("/api/cash/transactions", json=body, headers={**tenant, "Idempotency-Key": key})
    assert r.status_code == 409, r.text
    assert (await db.execute(select(func.count()).select_from(Transaction).where(
        Transaction.comment == "idem-clock"))).scalar_one() == 0


# ── 3. a Square charge with no checkout id is settled by the poll ─────────────
@pytest.mark.asyncio
async def test_square_checkout_with_lost_answer_is_found_by_the_poll(client, tenant, db, slow_square):
    tid = await _square_reader(client, tenant)
    oid = await _order(client, tenant)
    slow_square.lose_create_answer = True          # Square made the checkout; we never heard back
    r = await client.post("/api/payments/terminal/charge", json={"terminal_id": tid, "order_id": oid}, headers=tenant)
    assert r.status_code == 201 and r.json()["status"] == "pending", r.text
    assert r.json()["provider_ref"] is None
    slow_square.lose_create_answer = False
    co = next(iter(slow_square.checkouts.values()))
    co.update(status="COMPLETED", payment_ids=[])
    polled = (await client.get(f"/api/payments/terminal/charges/{r.json()['id']}", headers=tenant)).json()
    assert polled["status"] == "succeeded", polled
    assert polled["provider_ref"] == co["id"]
    db.expire_all()
    assert (await db.execute(select(func.count()).select_from(Payment).where(
        Payment.order_id == oid, Payment.kind == "payment"))).scalar_one() == 1


# ── 4. a superseded charge canceled at the provider stops being canceled ──────
@pytest.mark.asyncio
async def test_superseded_charge_canceled_at_the_provider_is_settled_once(client, tenant, db, flaky_stripe):
    tid = await _stripe_reader(client, tenant)
    oid = await _order(client, tenant)
    ch = (await client.post("/api/payments/terminal/charge", json={"terminal_id": tid, "order_id": oid},
                            headers=tenant)).json()
    await db.execute(update(TerminalCharge).where(TerminalCharge.id == ch["id"]).values(
        status="failed", error=svc.SUPERSEDED_NOTE))
    await db.commit()
    await svc.reconcile_superseded(db)
    assert len(flaky_stripe.cancels()) == 1
    assert (await _charge_row(db, ch["id"])).status == "canceled"
    await svc.reconcile_superseded(db)                  # the next sweep leaves it alone
    assert len(flaky_stripe.cancels()) == 1


# ── 5. unconfirmed / stale pending charges are canceled so the reader frees ───
@pytest.mark.asyncio
async def test_unconfirmed_charge_is_canceled_by_the_sweep_and_the_reader_frees(client, tenant, db, flaky_stripe):
    tid = await _stripe_reader(client, tenant)
    oid = await _order(client, tenant)
    flaky_stripe.fail_process = flaky_stripe.fail_cancel = True
    ch = (await client.post("/api/payments/terminal/charge", json={"terminal_id": tid, "order_id": oid},
                            headers=tenant)).json()
    assert ch["status"] == "pending" and ch["error"] == svc.UNCONFIRMED_NOTE
    flaky_stripe.fail_process = flaky_stripe.fail_cancel = False
    await svc.reconcile_superseded(db)                  # too soon: the customer may be tapping now
    assert (await _charge_row(db, ch["id"])).status == "pending"
    await _age_charge(db, ch["id"], 3)
    await svc.reconcile_superseded(db)
    assert (await _charge_row(db, ch["id"])).status == "canceled"
    assert flaky_stripe.pi["pi_1"]["status"] == "canceled"
    r = await client.post("/api/payments/terminal/charge", json={"terminal_id": tid, "order_id": oid}, headers=tenant)
    assert r.status_code == 201, r.text                 # the reader takes charges again


@pytest.mark.asyncio
async def test_stale_pending_charge_is_canceled_but_a_fresh_one_is_left(client, tenant, db, flaky_stripe):
    t1 = await _stripe_reader(client, tenant)
    o1, o2 = await _order(client, tenant), await _order(client, tenant)
    old = (await client.post("/api/payments/terminal/charge", json={"terminal_id": t1, "order_id": o1},
                             headers=tenant)).json()
    await _age_charge(db, old["id"], svc.STALE_PENDING_MINUTES + 1)
    await svc.reconcile_superseded(db)
    assert (await _charge_row(db, old["id"])).status == "canceled"
    fresh = (await client.post("/api/payments/terminal/charge", json={"terminal_id": t1, "order_id": o2},
                               headers=tenant)).json()
    await svc.reconcile_superseded(db)
    assert (await _charge_row(db, fresh["id"])).status == "pending"


@pytest.mark.asyncio
async def test_stale_charge_paid_meanwhile_is_booked_not_canceled(client, tenant, db, flaky_stripe):
    tid = await _stripe_reader(client, tenant)
    oid = await _order(client, tenant)
    ch = (await client.post("/api/payments/terminal/charge", json={"terminal_id": tid, "order_id": oid},
                            headers=tenant)).json()
    await _age_charge(db, ch["id"], svc.STALE_PENDING_MINUTES + 1)
    flaky_stripe.pi["pi_1"].update(status="succeeded", amount_received=flaky_stripe.pi["pi_1"]["amount"])
    await svc.reconcile_superseded(db)
    assert (await _charge_row(db, ch["id"])).status == "succeeded"
    assert not flaky_stripe.cancels()
