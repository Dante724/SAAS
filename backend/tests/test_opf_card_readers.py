"""F1–F4 — scan-to-cart lookup, card readers (Stripe Terminal / Square Terminal / simulated),
reader webhooks, and the live-money guardrail. Provider HTTP is mocked with httpx.MockTransport;
the live sandbox smoke tests at the bottom run only when real test credentials are in the env."""
import asyncio
import base64
import hashlib
import hmac
import json
import os
import time
import uuid

import httpx
import pytest
from sqlalchemy import func, select

from backend.config import settings
from backend.models import Payment, Sale, TerminalCharge, Transaction
from backend.services import terminal as svc

SQ_URL = "https://shop.example/api/payments/square/webhook"
SQ_OK_DEVICE = "9fa747a2-25ff-48ee-b078-04381f7c828f"


# ── Fakes ────────────────────────────────────────────────────────────────────
class FakeStripe:
    """Just enough of Stripe's REST API for the server-driven Terminal flow."""

    def __init__(self):
        self.calls: list[tuple[str, str, dict]] = []
        self.pi: dict[str, dict] = {}
        self.n = 0

    def __call__(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path
        form = dict(httpx.QueryParams(req.content.decode())) if req.content else {}
        self.calls.append((req.method, path, form))
        if req.url.host == "connect.squareupsandbox.com":
            return httpx.Response(500, json={})
        if path == "/v1/terminal/locations":
            return httpx.Response(200, json={"id": "tml_1"})
        if path == "/v1/terminal/readers" and req.method == "POST":
            if form.get("registration_code") != "simulated-wpe":
                return httpx.Response(400, json={"error": {"message": "Invalid registration code"}})
            return httpx.Response(200, json={"id": "tmr_1", "device_type": "simulated_wisepos_e"})
        if path == "/v1/payment_intents" and req.method == "POST":
            self.n += 1
            pid = f"pi_{self.n}"
            self.pi[pid] = {"id": pid, "status": "requires_payment_method", "amount": int(form["amount"]),
                            "currency": form["currency"], "last_payment_error": None}
            return httpx.Response(200, json=self.pi[pid])
        if path.endswith("/process_payment_intent"):
            return httpx.Response(200, json={"id": "tmr_1", "action": {"status": "in_progress"}})
        if path.endswith("/cancel_action"):
            return httpx.Response(200, json={"id": "tmr_1"})
        if path.startswith("/v1/payment_intents/"):
            pid = path.split("/")[3]
            pi = self.pi[pid]
            if path.endswith("/cancel"):
                if pi["status"] == "succeeded":
                    return httpx.Response(400, json={"error": {"message": "already succeeded"}})
                pi["status"] = "canceled"
            return httpx.Response(200, json=pi)
        if path.endswith("/present_payment_method"):
            pid = next(k for k, v in self.pi.items() if v["status"] == "requires_payment_method")
            if form.get("card_present[number]") == "4000000000000002":
                self.pi[pid]["last_payment_error"] = {"message": "Your card was declined."}
            else:
                self.pi[pid].update(status="succeeded", amount_received=self.pi[pid]["amount"])
            return httpx.Response(200, json={"id": "tmr_1"})
        if path == "/v1/refunds":
            return httpx.Response(200, json={"id": f"re_{len(self.calls)}", "amount": int(form["amount"])})
        if req.method == "DELETE":
            return httpx.Response(200, json={"deleted": True})
        return httpx.Response(404, json={"error": {"message": f"unmocked {req.method} {path}"}})

    def paths(self):
        return [p for _, p, _ in self.calls]


class FakeSquare:
    def __init__(self):
        self.calls: list[tuple[str, str, dict]] = []
        self.checkouts: dict[str, dict] = {}

    def __call__(self, req: httpx.Request) -> httpx.Response:
        assert req.url.host == "connect.squareupsandbox.com"   # never production
        body = json.loads(req.content or b"{}")
        self.calls.append((req.method, req.url.path, body))
        path = req.url.path
        if path == "/v2/terminals/checkouts":
            cid = f"chk_{len(self.checkouts) + 1}"
            co = dict(body["checkout"], id=cid, status="PENDING")
            self.checkouts[cid] = co
            return httpx.Response(200, json={"checkout": co})
        if path.startswith("/v2/terminals/checkouts/"):
            cid = path.split("/")[4]
            co = self.checkouts[cid]
            if path.endswith("/cancel"):
                co["status"] = "CANCELED"
            return httpx.Response(200, json={"checkout": co})
        if path == "/v2/refunds":
            return httpx.Response(200, json={"refund": {"id": "sqref_1", "status": "PENDING"}})
        return httpx.Response(404, json={"errors": [{"detail": "unmocked"}]})


@pytest.fixture
def stripe(monkeypatch):
    fake = FakeStripe()
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_fake", raising=False)
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", "whsec_fake", raising=False)
    monkeypatch.setattr(svc, "_TRANSPORT", httpx.MockTransport(fake))
    return fake


@pytest.fixture
def square(monkeypatch):
    fake = FakeSquare()
    monkeypatch.setattr(settings, "SQUARE_ACCESS_TOKEN", "sq-sandbox-token", raising=False)
    monkeypatch.setattr(settings, "SQUARE_ENV", "sandbox", raising=False)
    monkeypatch.setattr(settings, "SQUARE_WEBHOOK_SIGNATURE_KEY", "sq-sig-key", raising=False)
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://shop.example", raising=False)
    monkeypatch.setattr(svc, "_TRANSPORT", httpx.MockTransport(fake))
    return fake


# ── Helpers ──────────────────────────────────────────────────────────────────
async def _tenant(client):
    email = f"admin-{uuid.uuid4().hex[:8]}@test.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Other Co", "name": "Admin", "email": email, "password": "pw-test-12345"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _card_account(client, h, fee_pct=0):
    await client.post("/api/cash/accounts", json={"name": "Card", "payment_option": "card", "fee_pct": fee_pct},
                      headers=h)
    return [a for a in (await client.get("/api/cash/accounts", headers=h)).json()["items"] if a["name"] == "Card"][0]["id"]


async def _order(client, h, total=100):
    cid = (await client.post("/api/clients", json={"name": "C", "phone": "+1"}, headers=h)).json()["id"]
    oid = (await client.post("/api/orders", json={"client_id": cid}, headers=h)).json()["id"]
    await client.post(f"/api/orders/{oid}/lines", json={
        "line_type": "labour", "description": "Fix", "qty": 1, "unit_price": total}, headers=h)
    return oid


async def _owed(db, oid):
    """Balance due by the shared definition (services/order_money: net of refunds)."""
    from backend.models import Order
    from backend.services.order_money import balance_due
    db.expire_all()
    return await balance_due(db, (await db.execute(select(Order).where(Order.id == oid))).scalar_one())


async def _reader(client, h, provider="stripe", **kw):
    body = {"provider": provider, "label": "Front desk", **kw}
    if provider == "stripe":
        body.setdefault("address", {"line1": "1 Main St", "city": "Austin", "state": "TX",
                                    "postal_code": "78701", "country": "US"})
    r = await client.post("/api/payments/terminal/readers", json=body, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


async def _charge(client, h, reader_id, **kw):
    return await client.post("/api/payments/terminal/charge", json={"terminal_id": reader_id, **kw}, headers=h)


def _stripe_sig(raw: bytes, secret="whsec_fake"):
    ts = str(int(time.time()))
    return f"t={ts},v1=" + hmac.new(secret.encode(), f"{ts}.".encode() + raw, hashlib.sha256).hexdigest()


async def _stripe_event(client, kind, pi, event_id=None, sig=True, secret="whsec_fake"):
    raw = json.dumps({"id": event_id or f"evt_{uuid.uuid4().hex[:8]}", "type": kind,
                      "data": {"object": pi}}).encode()
    headers = {"content-type": "application/json"}
    if sig:
        headers["stripe-signature"] = _stripe_sig(raw, secret)
    return await client.post("/api/payments/stripe/webhook", content=raw, headers=headers)


def _sq_sig(raw: bytes, key="sq-sig-key"):
    return base64.b64encode(hmac.new(key.encode(), SQ_URL.encode() + raw, hashlib.sha256).digest()).decode()


async def _square_event(client, checkout, event_id=None, key="sq-sig-key"):
    raw = json.dumps({"event_id": event_id or f"sqevt_{uuid.uuid4().hex[:8]}", "type": "terminal.checkout.updated",
                      "data": {"object": {"checkout": checkout}}}).encode()
    return await client.post("/api/payments/square/webhook", content=raw, headers={
        "content-type": "application/json", "x-square-hmacsha256-signature": _sq_sig(raw, key)})


async def _payments(db, oid, kind="payment"):
    return (await db.execute(select(Payment).where(Payment.order_id == oid, Payment.kind == kind))).scalars().all()


async def _emp_with_role(client, h, perms):
    role = (await client.post("/api/staff/roles", json={"name": f"R{uuid.uuid4().hex[:4]}", "permissions": perms},
                              headers=h)).json()
    email = f"e-{uuid.uuid4().hex[:6]}@t.dev"
    await client.post("/api/staff/employees", json={
        "name": "Emp", "email": email, "password": "pw-test-12345", "role_id": role["id"]}, headers=h)
    tok = (await client.post("/api/auth/token", data={"username": email, "password": "pw-test-12345"})).json()
    return {"Authorization": f"Bearer {tok['access_token']}"}


# ── F2: Stripe Terminal ──────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_register_stripe_reader_uses_simulated_code_and_a_location(client, tenant, stripe):
    t = await _reader(client, tenant)
    assert t["reader_id"] == "tmr_1" and t["provider_location"] == "tml_1" and t["test_mode"] is True
    reg = [f for m, p, f in stripe.calls if p == "/v1/terminal/readers"][0]
    assert reg["registration_code"] == "simulated-wpe" and reg["location"] == "tml_1"
    # A second reader reuses the shop's Stripe location instead of creating another.
    assert stripe.paths().count("/v1/terminal/locations") == 1
    readers = (await client.get("/api/payments/terminal/readers", headers=tenant)).json()["items"]
    assert [r["id"] for r in readers] == [t["id"]]
    dup = await client.post("/api/payments/terminal/readers", json={"provider": "stripe", "label": "x"}, headers=tenant)
    assert dup.status_code == 409


@pytest.mark.asyncio
async def test_charge_creates_card_present_intent_and_pushes_it_to_the_reader(client, tenant, stripe):
    await _card_account(client, tenant)
    t = await _reader(client, tenant)
    oid = await _order(client, tenant, 100)
    r = await _charge(client, tenant, t["id"], order_id=oid)
    assert r.status_code == 201, r.text
    ch = r.json()
    assert ch["status"] == "pending" and ch["status_label"] == "Waiting for card" and ch["amount"] == 100
    pi_form = [f for m, p, f in stripe.calls if p == "/v1/payment_intents"][0]
    assert pi_form["payment_method_types[]"] == "card_present" and pi_form["amount"] == "10000"
    assert "/v1/terminal/readers/tmr_1/process_payment_intent" in stripe.paths()
    # A second charge while one waits is refused (double tap / two tills).
    assert (await _charge(client, tenant, t["id"], order_id=oid)).status_code == 409


@pytest.mark.asyncio
async def test_charge_cannot_exceed_balance(client, tenant, stripe):
    await _card_account(client, tenant)
    t = await _reader(client, tenant)
    oid = await _order(client, tenant, 50)
    assert (await _charge(client, tenant, t["id"], order_id=oid, amount=60)).status_code == 422
    assert (await _charge(client, tenant, t["id"], order_id=oid, amount=-1)).status_code == 400
    assert (await _charge(client, tenant, t["id"])).status_code == 422          # neither order nor sale


@pytest.mark.asyncio
async def test_webhook_posts_payment_exactly_once(client, tenant, stripe, db):
    await _card_account(client, tenant)
    t = await _reader(client, tenant)
    oid = await _order(client, tenant, 100)
    ch = (await _charge(client, tenant, t["id"], order_id=oid)).json()
    pi = dict(stripe.pi[ch["provider_ref"]], status="succeeded", amount_received=10000)

    r = await _stripe_event(client, "payment_intent.succeeded", pi, event_id="evt_A")
    assert r.status_code == 200 and r.json()["status"] == "succeeded", r.text
    # Same event redelivered, a different event for the same intent, and a status poll: no new money.
    assert (await _stripe_event(client, "payment_intent.succeeded", pi, event_id="evt_A")).json()["duplicate"]
    assert (await _stripe_event(client, "payment_intent.succeeded", pi, event_id="evt_B")).status_code == 200
    stripe.pi[ch["provider_ref"]].update(status="succeeded", amount_received=10000)
    assert (await client.get(f"/api/payments/terminal/charges/{ch['id']}", headers=tenant)).json()["status"] == "succeeded"

    pays = await _payments(db, oid)
    assert len(pays) == 1 and pays[0].method == "card" and pays[0].provider_ref == ch["provider_ref"]
    assert await _owed(db, oid) == 0
    txns = (await db.execute(select(func.count()).select_from(Transaction).where(
        Transaction.ref_id == oid, Transaction.txn_type == "invoice_payment"))).scalar()
    assert txns == 1


@pytest.mark.asyncio
async def test_webhook_signature_invalid_or_missing_is_rejected(client, tenant, stripe, db):
    await _card_account(client, tenant)
    t = await _reader(client, tenant)
    oid = await _order(client, tenant, 100)
    ch = (await _charge(client, tenant, t["id"], order_id=oid)).json()
    pi = dict(stripe.pi[ch["provider_ref"]], status="succeeded", amount_received=10000)
    assert (await _stripe_event(client, "payment_intent.succeeded", pi, secret="whsec_wrong")).status_code == 400
    assert (await _stripe_event(client, "payment_intent.succeeded", pi, sig=False)).status_code == 400
    assert await _payments(db, oid) == []
    # An intent we never sent to a reader (e.g. behind a hosted checkout) is acknowledged and ignored.
    other = await _stripe_event(client, "payment_intent.succeeded", {"id": "pi_unknown", "status": "succeeded"})
    assert other.status_code == 200 and other.json()["unmatched"]


@pytest.mark.asyncio
async def test_amount_less_than_charged_is_never_booked_and_frees_the_reader(client, tenant, stripe, db):
    await _card_account(client, tenant)
    t = await _reader(client, tenant)
    oid = await _order(client, tenant, 100)
    ch = (await _charge(client, tenant, t["id"], order_id=oid)).json()
    pi = dict(stripe.pi[ch["provider_ref"]], status="succeeded", amount_received=100)
    r = await _stripe_event(client, "payment_intent.succeeded", pi)
    assert r.status_code == 200 and r.json()["status"] == "failed"
    got = (await client.get(f"/api/payments/terminal/charges/{ch['id']}", headers=tenant)).json()
    assert "dashboard" in got["error"]
    assert await _payments(db, oid) == []
    assert (await _charge(client, tenant, t["id"], order_id=oid)).status_code == 201   # reader not stuck


@pytest.mark.asyncio
async def test_tip_on_the_reader_is_booked(client, tenant, stripe, db):
    await _card_account(client, tenant)
    t = await _reader(client, tenant)
    oid = await _order(client, tenant, 100)
    ch = (await _charge(client, tenant, t["id"], order_id=oid)).json()
    pi = dict(stripe.pi[ch["provider_ref"]], status="succeeded", amount_received=11500)
    assert (await _stripe_event(client, "payment_intent.succeeded", pi)).json()["status"] == "succeeded"
    pays = await _payments(db, oid)
    assert [p.amount for p in pays] == [115]
    got = (await client.get(f"/api/payments/terminal/charges/{ch['id']}", headers=tenant)).json()
    assert got["amount"] == 115


@pytest.mark.asyncio
async def test_decline_records_nothing_and_cancels_the_intent(client, tenant, stripe, db):
    await _card_account(client, tenant)
    t = await _reader(client, tenant)
    oid = await _order(client, tenant, 100)
    ch = (await _charge(client, tenant, t["id"], order_id=oid)).json()
    pi = dict(stripe.pi[ch["provider_ref"]], last_payment_error={"message": "Your card was declined."})
    r = await _stripe_event(client, "payment_intent.payment_failed", pi)
    assert r.json()["status"] == "declined"
    got = (await client.get(f"/api/payments/terminal/charges/{ch['id']}", headers=tenant)).json()
    assert got["status_label"] == "Declined" and "declined" in got["error"].lower()
    assert f"/v1/payment_intents/{ch['provider_ref']}/cancel" in stripe.paths()
    assert await _payments(db, oid) == [] and await _owed(db, oid) == 100
    # The reader is free again: a retry makes a new charge.
    assert (await _charge(client, tenant, t["id"], order_id=oid)).status_code == 201


@pytest.mark.asyncio
async def test_simulated_present_card_approve_and_decline_via_test_helper(client, tenant, stripe, db):
    await _card_account(client, tenant)
    t = await _reader(client, tenant)
    oid = await _order(client, tenant, 100)
    ch = (await _charge(client, tenant, t["id"], order_id=oid, amount=40)).json()
    r = await client.post(f"/api/payments/terminal/charges/{ch['id']}/simulate", json={"outcome": "decline"}, headers=tenant)
    assert r.json()["status"] == "declined"
    present = [f for m, p, f in stripe.calls if p.endswith("/present_payment_method")]
    assert present[0]["card_present[number]"] == "4000000000000002"
    ch2 = (await _charge(client, tenant, t["id"], order_id=oid, amount=40)).json()
    r = await client.post(f"/api/payments/terminal/charges/{ch2['id']}/simulate", json={"outcome": "approve"}, headers=tenant)
    assert r.json()["status"] == "succeeded"
    assert await _owed(db, oid) == 60


@pytest.mark.asyncio
async def test_simulate_endpoint_is_refused_in_production(client, tenant, stripe, monkeypatch):
    await _card_account(client, tenant)
    t = await _reader(client, tenant)
    oid = await _order(client, tenant, 100)
    ch = (await _charge(client, tenant, t["id"], order_id=oid)).json()
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    r = await client.post(f"/api/payments/terminal/charges/{ch['id']}/simulate", json={"outcome": "approve"}, headers=tenant)
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_cancel_waiting_charge(client, tenant, stripe, db):
    await _card_account(client, tenant)
    t = await _reader(client, tenant)
    oid = await _order(client, tenant, 100)
    ch = (await _charge(client, tenant, t["id"], order_id=oid)).json()
    r = await client.post(f"/api/payments/terminal/charges/{ch['id']}/cancel", headers=tenant)
    assert r.json()["status"] == "canceled"
    assert "/v1/terminal/readers/tmr_1/cancel_action" in stripe.paths()
    assert stripe.pi[ch["provider_ref"]]["status"] == "canceled"
    assert (await client.post(f"/api/payments/terminal/charges/{ch['id']}/cancel", headers=tenant)).status_code == 409
    assert await _payments(db, oid) == []


@pytest.mark.asyncio
async def test_cancel_after_card_was_taken_settles_instead(client, tenant, stripe, db):
    """The customer taps just as staff press Cancel: the money is recorded, not lost."""
    await _card_account(client, tenant)
    t = await _reader(client, tenant)
    oid = await _order(client, tenant, 100)
    ch = (await _charge(client, tenant, t["id"], order_id=oid)).json()
    stripe.pi[ch["provider_ref"]].update(status="succeeded", amount_received=10000)
    r = await client.post(f"/api/payments/terminal/charges/{ch['id']}/cancel", headers=tenant)
    assert r.json()["status"] == "succeeded"
    assert len(await _payments(db, oid)) == 1


@pytest.mark.asyncio
async def test_clear_reader_cancels_its_waiting_charge(client, tenant, stripe):
    await _card_account(client, tenant)
    t = await _reader(client, tenant)
    oid = await _order(client, tenant, 100)
    await _charge(client, tenant, t["id"], order_id=oid)
    r = await client.post(f"/api/payments/terminal/readers/{t['id']}/clear", headers=tenant)
    assert r.json()["canceled"] == 1
    assert (await _charge(client, tenant, t["id"], order_id=oid)).status_code == 201


@pytest.mark.asyncio
async def test_refund_partial_then_rest(client, tenant, stripe, db):
    await _card_account(client, tenant)
    t = await _reader(client, tenant)
    oid = await _order(client, tenant, 100)
    ch = (await _charge(client, tenant, t["id"], order_id=oid)).json()
    await client.post(f"/api/payments/terminal/charges/{ch['id']}/simulate", json={"outcome": "approve"}, headers=tenant)
    url = f"/api/payments/terminal/charges/{ch['id']}/refund"
    r = await client.post(url, json={"amount": 30}, headers=tenant)
    assert r.status_code == 200 and r.json()["refunded_amount"] == 30
    assert [f["amount"] for m, p, f in stripe.calls if p == "/v1/refunds"] == ["3000"]
    assert await _owed(db, oid) == 30
    assert (await client.post(url, json={"amount": 80}, headers=tenant)).status_code == 422
    assert (await client.post(url, json={}, headers=tenant)).json()["refunded_amount"] == 100
    assert (await client.post(url, json={}, headers=tenant)).status_code == 400
    refunds = await _payments(db, oid, "refund")
    assert sorted(p.amount for p in refunds) == [30, 70] and all(p.provider_ref for p in refunds)


@pytest.mark.asyncio
async def test_refund_requires_approved_charge(client, tenant, stripe):
    await _card_account(client, tenant)
    t = await _reader(client, tenant)
    oid = await _order(client, tenant, 100)
    ch = (await _charge(client, tenant, t["id"], order_id=oid)).json()
    assert (await client.post(f"/api/payments/terminal/charges/{ch['id']}/refund", json={}, headers=tenant)).status_code == 409


@pytest.mark.asyncio
async def test_remove_reader_blocked_while_charge_waits(client, tenant, stripe):
    await _card_account(client, tenant)
    t = await _reader(client, tenant)
    oid = await _order(client, tenant, 100)
    ch = (await _charge(client, tenant, t["id"], order_id=oid)).json()
    assert (await client.delete(f"/api/payments/terminal/readers/{t['id']}", headers=tenant)).status_code == 409
    await client.post(f"/api/payments/terminal/charges/{ch['id']}/cancel", headers=tenant)
    assert (await client.delete(f"/api/payments/terminal/readers/{t['id']}", headers=tenant)).status_code == 200
    assert (await client.get("/api/payments/terminal/readers", headers=tenant)).json()["items"] == []


@pytest.mark.asyncio
async def test_edit_reader_label_and_location(client, tenant, stripe):
    t = await _reader(client, tenant)
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    r = await client.patch(f"/api/payments/terminal/readers/{t['id']}", json={"label": "Back counter", "location_id": loc},
                           headers=tenant)
    assert r.json()["label"] == "Back counter" and r.json()["location_id"] == loc
    assert (await client.patch(f"/api/payments/terminal/readers/{t['id']}", json={"label": " "},
                               headers=tenant)).status_code == 422


# ── Counter sales (simulated reader, no account needed) ──────────────────────
async def _sale(client, h, price=25):
    r = await client.post("/api/sales", json={"lines": [
        {"kind": "service", "ref_id": None, "description": "Screen protector fit", "qty": 1, "unit_price": price}]},
        headers=h)
    assert r.status_code == 201 and r.json()["paid"] is False
    return r.json()["id"]


@pytest.mark.asyncio
async def test_sale_paid_on_simulated_reader(client, tenant, db):
    acc = await _card_account(client, tenant, fee_pct=2)
    t = await _reader(client, tenant, provider="simulated")
    sid = await _sale(client, tenant)
    ch = (await _charge(client, tenant, t["id"], sale_id=sid, account_id=acc)).json()
    assert ch["amount"] == 25 and ch["status"] == "pending"
    r = await client.post(f"/api/payments/terminal/charges/{ch['id']}/simulate", json={"outcome": "approve"}, headers=tenant)
    assert r.json()["status"] == "succeeded"
    sale = (await db.execute(select(Sale).where(Sale.id == sid))).scalar_one()
    assert sale.paid is True and sale.account_id == acc
    kinds = sorted(x.txn_type for x in (await db.execute(select(Transaction).where(Transaction.ref_id == sid))).scalars())
    assert kinds == ["fee", "income"]
    listed = (await client.get("/api/payments/terminal/charges?kind=sale&status=succeeded", headers=tenant)).json()["items"]
    assert [c["id"] for c in listed] == [ch["id"]] and listed[0]["document"].startswith("S-")
    assert (await client.get("/api/payments/terminal/charges?kind=order", headers=tenant)).json()["items"] == []
    # A card-paid sale can't be deleted until the card is refunded; then it can, and no more refunds.
    assert (await client.delete(f"/api/sales/{sid}", headers=tenant)).status_code == 409
    assert (await client.post(f"/api/payments/terminal/charges/{ch['id']}/refund", json={}, headers=tenant)).status_code == 200
    assert (await client.delete(f"/api/sales/{sid}", headers=tenant)).status_code == 200
    net = sum(x.amount for x in (await db.execute(select(Transaction).where(Transaction.ref_id == sid))).scalars()
              if (x.status or "active") != "deleted")
    assert net == 0
    sid = await _sale(client, tenant)
    # Paid sales can't be charged again; a decline leaves a fresh sale unpaid.
    ch1 = (await _charge(client, tenant, t["id"], sale_id=sid)).json()
    await client.post(f"/api/payments/terminal/charges/{ch1['id']}/simulate", json={"outcome": "approve"}, headers=tenant)
    assert (await _charge(client, tenant, t["id"], sale_id=sid)).status_code == 409
    sid2 = await _sale(client, tenant)
    ch2 = (await _charge(client, tenant, t["id"], sale_id=sid2)).json()
    await client.post(f"/api/payments/terminal/charges/{ch2['id']}/simulate", json={"outcome": "decline"}, headers=tenant)
    assert (await db.execute(select(Sale.paid).where(Sale.id == sid2))).scalar() is False


@pytest.mark.asyncio
async def test_simulated_reader_unavailable_when_deployed(client, tenant, monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "staging")
    r = await client.post("/api/payments/terminal/readers", json={"provider": "simulated", "label": "x"}, headers=tenant)
    assert r.status_code == 400


# ── F3: Square Terminal (sandbox) ────────────────────────────────────────────
@pytest.mark.asyncio
async def test_square_reader_needs_a_documented_sandbox_device(client, tenant, square):
    bad = await client.post("/api/payments/terminal/readers", json={
        "provider": "square", "label": "Sq", "device_id": "real-device"}, headers=tenant)
    assert bad.status_code == 422
    t = await _reader(client, tenant, provider="square", device_id=SQ_OK_DEVICE)
    assert t["reader_id"] == SQ_OK_DEVICE and t["test_mode"] is True


@pytest.mark.asyncio
async def test_square_checkout_and_webhook_posts_once(client, tenant, square, db):
    await _card_account(client, tenant)
    t = await _reader(client, tenant, provider="square", device_id=SQ_OK_DEVICE)
    oid = await _order(client, tenant, 20)
    ch = (await _charge(client, tenant, t["id"], order_id=oid)).json()
    sent = square.calls[0][2]
    assert sent["checkout"]["device_options"]["device_id"] == SQ_OK_DEVICE
    assert sent["checkout"]["amount_money"] == {"amount": 2000, "currency": "INR"}
    assert sent["idempotency_key"] == ch["id"]

    done = dict(square.checkouts[ch["provider_ref"]], status="COMPLETED", payment_ids=["sqpay_1"])
    r = await _square_event(client, done, event_id="sq-1")
    assert r.status_code == 200 and r.json()["status"] == "succeeded", r.text
    assert (await _square_event(client, done, event_id="sq-1")).json()["duplicate"]
    assert (await _square_event(client, done, event_id="sq-2")).status_code == 200
    assert len(await _payments(db, oid)) == 1 and await _owed(db, oid) == 0
    stored = (await db.execute(select(TerminalCharge).where(TerminalCharge.id == ch["id"]))).scalar_one()
    assert stored.provider_payment_id == "sqpay_1"
    # Refunds go through Square's Refunds API against that payment id.
    r = await client.post(f"/api/payments/terminal/charges/{ch['id']}/refund", json={"amount": 5}, headers=tenant)
    assert r.status_code == 200
    assert [b for m, p, b in square.calls if p == "/v2/refunds"][0]["payment_id"] == "sqpay_1"


@pytest.mark.asyncio
async def test_square_webhook_bad_hmac_rejected_and_cancel_works(client, tenant, square, db):
    await _card_account(client, tenant)
    t = await _reader(client, tenant, provider="square", device_id=SQ_OK_DEVICE)
    oid = await _order(client, tenant, 20)
    ch = (await _charge(client, tenant, t["id"], order_id=oid)).json()
    done = dict(square.checkouts[ch["provider_ref"]], status="COMPLETED", payment_ids=["sqpay_1"])
    assert (await _square_event(client, done, key="wrong")).status_code == 400
    assert await _payments(db, oid) == []
    r = await client.post(f"/api/payments/terminal/charges/{ch['id']}/cancel", headers=tenant)
    assert r.json()["status"] == "canceled"
    assert square.calls[-1][1].endswith("/cancel")


@pytest.mark.asyncio
async def test_square_canceled_by_buyer_via_poll(client, tenant, square):
    await _card_account(client, tenant)
    t = await _reader(client, tenant, provider="square", device_id=SQ_OK_DEVICE)
    oid = await _order(client, tenant, 20)
    ch = (await _charge(client, tenant, t["id"], order_id=oid)).json()
    square.checkouts[ch["provider_ref"]].update(status="CANCELED", cancel_reason="BUYER_CANCELED")
    got = (await client.get(f"/api/payments/terminal/charges/{ch['id']}", headers=tenant)).json()
    assert got["status"] == "canceled" and "customer" in got["error"].lower()


# ── F4: guardrail ────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_live_stripe_key_refused_outside_production(client, tenant, stripe, monkeypatch):
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_live_real")
    r = await client.post("/api/payments/terminal/readers", json={"provider": "stripe", "label": "x"}, headers=tenant)
    assert r.status_code == 403 and "ALLOW_LIVE_PAYMENTS" in r.json()["detail"]
    assert stripe.calls == []                                    # never reached Stripe
    cfg = (await client.get("/api/payments/terminal/config", headers=tenant)).json()
    assert cfg["providers"]["stripe"]["live_refused"] is True and cfg["test_mode"] is True
    # Saving live keys is refused too, and they are never echoed back.
    r = await client.put("/api/integrations/credentials", json={
        "provider": "stripe", "data": {"secret_key": "sk_live_real"}}, headers=tenant)
    assert r.status_code == 422 and "sk_live_real" not in r.text
    r = await client.put("/api/integrations/credentials", json={
        "provider": "square", "data": {"access_token": "tok", "env": "production"}}, headers=tenant)
    assert r.status_code == 422
    # Hosted checkouts obey the same rule.
    oid = await _order(client, tenant, 10)
    r = await client.post("/api/payments/checkout", json={"order_id": oid, "amount": 10}, headers=tenant)
    assert r.status_code == 502 and "ALLOW_LIVE_PAYMENTS" in r.json()["detail"]


@pytest.mark.asyncio
async def test_guard_lives_in_effective_creds(client, tenant, db, monkeypatch):
    """Every path that would use the keys is refused; read-only lookups still see them."""
    from backend.services import org_creds
    from backend.services.payment_guard import LivePaymentsRefused
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_live_env")
    with pytest.raises(LivePaymentsRefused):
        await org_creds.effective(db, me["org_id"], "stripe")
    assert (await org_creds.effective(db, me["org_id"], "stripe", read_only=True))["secret_key"] == "sk_live_env"
    assert await org_creds.effective(db, me["org_id"], "twilio") is not None     # non-payment providers untouched
    # A pay link revoked with live keys on staging is closed here without calling Stripe.
    from backend.models import PaymentIntent
    from backend.services.order_money import _expire_stripe_checkout
    i = PaymentIntent(org_id=me["org_id"], provider="stripe", provider_ref="cs_live_1", amount=5, status="pending")
    assert await _expire_stripe_checkout(db, i) is False


@pytest.mark.asyncio
async def test_test_keys_save_and_stay_masked(client, tenant):
    r = await client.put("/api/integrations/credentials", json={
        "provider": "stripe", "data": {"secret_key": "sk_test_abc", "webhook_secret": "whsec_x"}}, headers=tenant)
    assert r.status_code == 200 and "sk_test_abc" not in r.text and "whsec_x" not in r.text


def test_guard_allows_live_only_in_production_with_flag(monkeypatch):
    from backend.services import payment_guard as g
    live = {"secret_key": "sk_live_x"}
    monkeypatch.setattr(settings, "ALLOW_LIVE_PAYMENTS", True)
    with pytest.raises(g.LivePaymentsRefused):           # flag on but not production
        g.assert_allowed("stripe", live)
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    g.assert_allowed("stripe", live)                       # both: allowed
    assert g.test_mode("stripe", live) is False
    monkeypatch.setattr(settings, "ALLOW_LIVE_PAYMENTS", False)
    with pytest.raises(g.LivePaymentsRefused):           # production without the flag
        g.assert_allowed("square", {"env": "production"})
    g.assert_allowed("stripe", {"secret_key": "sk_test_x"})    # test keys always fine
    assert g.test_mode("square", {"env": "sandbox"}) is True


@pytest.mark.asyncio
async def test_payments_config_reports_test_mode(client, tenant):
    assert (await client.get("/api/payments/config", headers=tenant)).json()["test_mode"] is True


# ── Tenant isolation ─────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_other_shop_cannot_see_or_use_readers_or_charges(client, tenant, stripe, db, monkeypatch):
    await _card_account(client, tenant)
    t = await _reader(client, tenant)
    oid = await _order(client, tenant, 100)
    ch = (await _charge(client, tenant, t["id"], order_id=oid)).json()
    other = await _tenant(client)
    await _card_account(client, other)
    their_order = await _order(client, other, 100)
    assert (await client.get("/api/payments/terminal/readers", headers=other)).json()["items"] == []
    assert (await _charge(client, other, t["id"], order_id=their_order)).status_code == 404
    for path in (f"/charges/{ch['id']}",):
        assert (await client.get("/api/payments/terminal" + path, headers=other)).status_code == 404
    for path in (f"/charges/{ch['id']}/cancel", f"/charges/{ch['id']}/refund", f"/charges/{ch['id']}/simulate",
                 f"/readers/{t['id']}/clear"):
        assert (await client.post("/api/payments/terminal" + path, json={}, headers=other)).status_code == 404
    assert (await client.patch(f"/api/payments/terminal/readers/{t['id']}", json={"label": "x"}, headers=other)).status_code == 404
    assert (await client.delete(f"/api/payments/terminal/readers/{t['id']}", headers=other)).status_code == 404
    assert (await client.get("/api/payments/terminal/charges", headers=other)).json()["items"] == []
    # Their own reader can't charge our order either.
    their_reader = await _reader(client, other, provider="simulated")
    assert (await _charge(client, other, their_reader["id"], order_id=oid)).status_code == 404
    # A webhook for our charge signed with another shop's own secret is rejected: the event is
    # verified with the secret of the shop that owns the charge, never one named in the payload.
    from backend.services import org_creds
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    them = (await client.get("/api/auth/me", headers=other)).json()
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", "")
    await org_creds.set_creds(db, me["org_id"], "stripe", {"secret_key": "sk_test_fake", "webhook_secret": "whsec_own"})
    await org_creds.set_creds(db, them["org_id"], "stripe", {"secret_key": "sk_test_fake", "webhook_secret": "whsec_theirs"})
    pi = dict(stripe.pi[ch["provider_ref"]], status="succeeded", amount_received=10000,
              metadata={"org_id": them["org_id"]})
    assert (await _stripe_event(client, "payment_intent.succeeded", pi, secret="whsec_theirs")).status_code == 400
    assert await _payments(db, oid) == []
    assert (await _stripe_event(client, "payment_intent.succeeded", pi, secret="whsec_own")).status_code == 200


# ── Permissions ──────────────────────────────────────────────────────────────
_ALL = {"can_take_card_payments": True, "can_manage_terminals": True, "can_refund_card_payments": True,
        "can_view_finance": True, "can_create_sales": True}


@pytest.mark.asyncio
async def test_permissions_gate_every_endpoint(client, tenant, stripe):
    await _card_account(client, tenant)
    t = await _reader(client, tenant)
    oid = await _order(client, tenant, 100)
    ch = (await _charge(client, tenant, t["id"], order_id=oid)).json()
    base = "/api/payments/terminal"

    no_take = await _emp_with_role(client, tenant, {**_ALL, "can_take_card_payments": False})
    assert (await client.post(f"{base}/charge", json={"terminal_id": t["id"], "order_id": oid}, headers=no_take)).status_code == 403
    for p in (f"/charges/{ch['id']}", "/charges"):
        assert (await client.get(base + p, headers=no_take)).status_code == 403
    for p in (f"/charges/{ch['id']}/cancel", f"/charges/{ch['id']}/simulate", f"/readers/{t['id']}/clear"):
        assert (await client.post(base + p, json={}, headers=no_take)).status_code == 403

    nobody = await _emp_with_role(client, tenant, {**_ALL, "can_take_card_payments": False, "can_manage_terminals": False})
    for p in ("/readers", "/config"):
        assert (await client.get(base + p, headers=nobody)).status_code == 403

    no_manage = await _emp_with_role(client, tenant, {**_ALL, "can_manage_terminals": False})
    assert (await client.get(f"{base}/readers", headers=no_manage)).status_code == 200   # cashier still sees readers
    assert (await client.post(f"{base}/readers", json={"provider": "simulated", "label": "x"}, headers=no_manage)).status_code == 403
    assert (await client.patch(f"{base}/readers/{t['id']}", json={"label": "x"}, headers=no_manage)).status_code == 403
    assert (await client.delete(f"{base}/readers/{t['id']}", headers=no_manage)).status_code == 403

    no_refund = await _emp_with_role(client, tenant, {**_ALL, "can_refund_card_payments": False})
    assert (await client.post(f"{base}/charges/{ch['id']}/refund", json={}, headers=no_refund)).status_code == 403

    no_finance = await _emp_with_role(client, tenant, {**_ALL, "can_view_finance": False})
    assert (await client.post(f"{base}/charge", json={"terminal_id": t["id"], "order_id": oid}, headers=no_finance)).status_code == 403
    no_sales = await _emp_with_role(client, tenant, {**_ALL, "can_create_sales": False})
    assert (await client.post(f"{base}/charge", json={"terminal_id": t["id"], "sale_id": "x"}, headers=no_sales)).status_code == 403


def test_new_codes_default_off_for_staff_templates():
    from backend.services.permissions import ALL_CODES, new_role_permissions
    assert {"can_take_card_payments", "can_manage_terminals", "can_refund_card_payments"} <= ALL_CODES
    spec = new_role_permissions("Specialist")
    assert spec["can_take_card_payments"] is True
    assert spec["can_manage_terminals"] is False and spec["can_refund_card_payments"] is False
    assert new_role_permissions("Manager")["can_manage_terminals"] is True


# ── F1: scan-to-cart lookup ──────────────────────────────────────────────────
async def _product(client, h, **kw):
    r = await client.post("/api/catalog/products", json={"name": kw.pop("name", "Cable"), "sell_price": 9.5, **kw}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.mark.asyncio
async def test_lookup_matches_sku_barcode_code_case_insensitively(client, tenant):
    pid = await _product(client, tenant, sku="USB-C-1M", barcode="0123456789012", code="CBL1")
    for code, on in (("usb-c-1m", "sku"), ("0123456789012", "barcode"), ("cbl1", "code")):
        items = (await client.get(f"/api/sales/lookup?code={code}", headers=tenant)).json()["items"]
        assert [i["id"] for i in items] == [pid] and items[0]["matched_on"] == on and items[0]["sell_price"] == 9.5
    assert (await client.get("/api/sales/lookup?code=nope", headers=tenant)).json()["items"] == []
    assert (await client.get("/api/sales/lookup?code=%20", headers=tenant)).status_code == 422


@pytest.mark.asyncio
async def test_lookup_returns_every_match_for_a_picker(client, tenant):
    a = await _product(client, tenant, name="Case red", barcode="555")
    b = await _product(client, tenant, name="Case blue", sku="555")
    items = (await client.get("/api/sales/lookup?code=555", headers=tenant)).json()["items"]
    assert sorted(i["id"] for i in items) == sorted([a, b])


@pytest.mark.asyncio
async def test_lookup_matches_mpn_when_the_column_exists(client, tenant, db):
    from backend.models import Part
    if not hasattr(Part, "mpn"):
        pytest.skip("Part.mpn arrives with workstream A")
    pid = await _product(client, tenant)
    part = (await db.execute(select(Part).where(Part.id == pid))).scalar_one()
    part.mpn = "MPN-77"
    await db.commit()
    items = (await client.get("/api/sales/lookup?code=mpn-77", headers=tenant)).json()["items"]
    assert items[0]["id"] == pid and items[0]["matched_on"] == "mpn"


@pytest.mark.asyncio
async def test_lookup_is_tenant_scoped_and_permissioned(client, tenant):
    await _product(client, tenant, sku="ONLY-MINE")
    other = await _tenant(client)
    assert (await client.get("/api/sales/lookup?code=ONLY-MINE", headers=other)).json()["items"] == []
    cashier = await _emp_with_role(client, tenant, {"can_create_sales": False})
    assert (await client.get("/api/sales/lookup?code=ONLY-MINE", headers=cashier)).status_code == 403


# ── Live sandbox smoke tests (skipped unless real test credentials are present) ──
@pytest.mark.asyncio
@pytest.mark.skipif(not os.environ.get("STRIPE_TEST_SECRET_KEY", "").startswith("sk_test_"),
                    reason="set STRIPE_TEST_SECRET_KEY=sk_test_… to run against Stripe's test mode")
async def test_live_stripe_sandbox_simulated_reader_roundtrip(client, tenant, monkeypatch):
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", os.environ["STRIPE_TEST_SECRET_KEY"])
    monkeypatch.setattr(svc, "_TRANSPORT", None)
    await _card_account(client, tenant)
    t = await _reader(client, tenant)
    oid = await _order(client, tenant, 12)
    ch = (await _charge(client, tenant, t["id"], order_id=oid)).json()
    r = await client.post(f"/api/payments/terminal/charges/{ch['id']}/simulate", json={"outcome": "approve"}, headers=tenant)
    for _ in range(10):
        if r.json()["status"] != "pending":
            break
        await asyncio.sleep(1)
        r = await client.get(f"/api/payments/terminal/charges/{ch['id']}", headers=tenant)
    assert r.json()["status"] == "succeeded", r.text
    await client.delete(f"/api/payments/terminal/readers/{t['id']}", headers=tenant)


@pytest.mark.asyncio
@pytest.mark.skipif(not os.environ.get("SQUARE_SANDBOX_TOKEN"),
                    reason="set SQUARE_SANDBOX_TOKEN to run against the Square sandbox")
async def test_live_square_sandbox_checkout_roundtrip(client, tenant, monkeypatch):
    monkeypatch.setattr(settings, "SQUARE_ACCESS_TOKEN", os.environ["SQUARE_SANDBOX_TOKEN"])
    monkeypatch.setattr(settings, "SQUARE_ENV", "sandbox")
    monkeypatch.setattr(svc, "_TRANSPORT", None)
    r = await client.put("/api/staff/company", json={"currency_code": "USD"}, headers=tenant)  # sandbox is USD
    assert r.status_code == 200, r.text
    await _card_account(client, tenant)
    t = await _reader(client, tenant, provider="square", device_id=SQ_OK_DEVICE)
    oid = await _order(client, tenant, 5)
    ch = (await _charge(client, tenant, t["id"], order_id=oid)).json()
    assert ch["status"] == "pending", ch
    status = "pending"
    for _ in range(15):
        await asyncio.sleep(2)
        status = (await client.get(f"/api/payments/terminal/charges/{ch['id']}", headers=tenant)).json()["status"]
        if status != "pending":
            break
    assert status == "succeeded"
