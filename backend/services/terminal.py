"""F2/F3 — card readers, server-driven (Stripe Terminal and Square Terminal).

Tekly never talks to the reader itself. To take a card payment it asks the provider to push the
amount to a registered reader (Stripe: a card_present PaymentIntent + process_payment_intent;
Square: a Terminal checkout). The reader shows "tap or insert card"; the outcome comes back by
webhook (payment_intent.*, terminal.checkout.updated) and, for local setups where webhooks can't
arrive, by polling the provider. Both routes settle through settle_succeeded(), which records the
money exactly once: a Payment against the work order, or the takings of a counter sale.

A third provider, "simulated", needs no account and only exists outside production/staging, so
the whole flow (POS, work order drawer, e2e tests) runs on a laptop.

Every call refuses live credentials unless live payments are switched on (payment_guard).
"""
import logging
import uuid
from typing import Optional

import httpx
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models import (
    Account,
    CashFlowItem,
    Invoice,
    Order,
    Organisation,
    Payment,
    PaymentTerminal,
    Sale,
    TerminalCharge,
)
from backend.services import payment_guard
from backend.services.money import THREE_DECIMAL, ZERO_DECIMAL, minor_units, q2
from backend.services.dates import now_utc

log = logging.getLogger("tekly.terminal")

PROVIDERS = ("stripe", "square", "simulated")
SQUARE_VERSION = "2024-06-04"
STRIPE_API = "https://api.stripe.com"

# Square's documented sandbox Terminal devices (developer.squareup.com/docs/devtools/sandbox/testing).
# The device id decides the outcome; amounts above 25.00 USD fail in the sandbox.
SQUARE_SANDBOX_DEVICES = [
    {"id": "9fa747a2-25ff-48ee-b078-04381f7c828f", "label": "Approves the card payment"},
    {"id": "22cd266c-6246-4c06-9983-67f0c26346b0", "label": "Approves with a 20% tip"},
    {"id": "841100b9-ee60-4537-9bcf-e30b2ba5e215", "label": "Customer cancels"},
    {"id": "0a956d49-619a-4530-8e5e-8eac603ffc5e", "label": "Times out at once"},
    {"id": "da40d603-c2ea-4a65-8cfd-f42e36dab0c7", "label": "Never picks up (cancel it yourself)"},
]
STRIPE_TEST_REGISTRATION_CODE = "simulated-wpe"
STRIPE_TEST_CARDS = {"approve": "4242424242424242", "decline": "4000000000000002"}

OPEN = ("pending",)
FINAL_FAILED = ("declined", "canceled", "failed")

# Tests swap this for httpx.MockTransport; None = the real network.
_TRANSPORT: Optional[httpx.AsyncBaseTransport] = None


class TerminalError(HTTPException):
    """A provider or validation problem the cashier can read."""

    def __init__(self, status: int, message: str):
        super().__init__(status, message)


def _client(timeout: float = 15.0) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=timeout, transport=_TRANSPORT)


# ── Credentials ──────────────────────────────────────────────────────────────
async def provider_creds(db: AsyncSession, org_id: str, provider: str) -> dict:
    """The shop's effective keys for a reader provider, refusing live keys where they're not allowed."""
    if provider == "simulated":
        if settings.is_deployed:
            raise TerminalError(400, "The simulated reader is only for local testing.")
        return {}
    from backend.services import org_creds
    try:
        creds = await org_creds.effective(db, org_id, provider, own_only=True)   # review P8
    except payment_guard.LivePaymentsRefused as e:
        raise TerminalError(403, str(e)) from e
    if provider == "stripe" and not creds.get("secret_key"):
        raise TerminalError(400, "Add your own Stripe keys in Settings › Integrations first.")
    if provider == "square" and not creds.get("access_token"):
        raise TerminalError(400, "Add your own Square keys in Settings › Integrations first.")
    return creds


async def readiness(db: AsyncSession, org_id: str) -> dict:
    """What the Settings card shows: which providers can register readers, and in which mode."""
    from backend.services import org_creds
    out = {}
    for p in ("stripe", "square"):
        c = await org_creds.effective(db, org_id, p, read_only=True, own_only=True)
        configured = bool(c.get("secret_key") if p == "stripe" else c.get("access_token"))
        live = payment_guard.creds_are_live(p, c)
        out[p] = {"configured": configured, "test_mode": payment_guard.test_mode(p, c),
                  "live_refused": configured and live and not payment_guard.live_payments_allowed()}
    out["simulated"] = {"configured": not settings.is_deployed, "test_mode": True, "live_refused": False}
    live_any = any(v["configured"] and not v["test_mode"] for v in out.values())
    return {"providers": out, "test_mode": not live_any,
            # Review S8: each shop registers its own webhook URL, verified before any lookup.
            "webhook_paths": {"stripe": f"/api/payments/stripe/webhook/{org_id}",
                              "square": f"/api/payments/square/webhook/{org_id}"},
            "square_sandbox_devices": SQUARE_SANDBOX_DEVICES,
            "stripe_test_registration_code": STRIPE_TEST_REGISTRATION_CODE}


# ── Provider HTTP ────────────────────────────────────────────────────────────
async def _stripe(creds: dict, method: str, path: str, data: Optional[dict] = None,
                  idempotency_key: Optional[str] = None) -> dict:
    headers = {"Idempotency-Key": idempotency_key} if idempotency_key else {}
    try:
        async with _client() as c:
            r = await c.request(method, STRIPE_API + path, data=data, headers=headers,
                                auth=(creds["secret_key"], ""))
    except httpx.HTTPError as e:
        log.warning("Stripe %s %s failed: %s", method, path, type(e).__name__)
        raise TerminalError(502, "Couldn't reach Stripe. Try again in a minute.") from e
    body = _json(r)
    if r.status_code >= 400:
        msg = ((body.get("error") or {}).get("message")) or f"Stripe error {r.status_code}"
        raise TerminalError(502, f"Stripe: {msg}")
    return body


def _square_base(creds: dict) -> str:
    return ("https://connect.squareup.com" if payment_guard.is_live_square(creds)
            else "https://connect.squareupsandbox.com")


async def _square(creds: dict, method: str, path: str, payload: Optional[dict] = None) -> dict:
    headers = {"Authorization": f"Bearer {creds['access_token']}", "Square-Version": SQUARE_VERSION}
    try:
        async with _client() as c:
            r = await c.request(method, _square_base(creds) + path, json=payload, headers=headers)
    except httpx.HTTPError as e:
        log.warning("Square %s %s failed: %s", method, path, type(e).__name__)
        raise TerminalError(502, "Couldn't reach Square. Try again in a minute.") from e
    body = _json(r)
    if r.status_code >= 400:
        errs = body.get("errors") or [{}]
        msg = errs[0].get("detail") or errs[0].get("code") or f"Square error {r.status_code}"
        raise TerminalError(502, f"Square: {msg}")
    return body


def _json(r: httpx.Response) -> dict:
    try:
        b = r.json()
        return b if isinstance(b, dict) else {}
    except ValueError:
        return {}


# ── Readers ──────────────────────────────────────────────────────────────────
def ser_terminal(t: PaymentTerminal) -> dict:
    return {"id": t.id, "provider": t.provider, "reader_id": t.reader_id, "label": t.label,
            "location_id": t.location_id, "provider_location": t.provider_location,
            "device_type": t.device_type, "test_mode": t.test_mode, "active": t.active,
            "created_at": t.created_at.isoformat() if t.created_at else None}


async def _stripe_location(db: AsyncSession, org_id: str, creds: dict, address: Optional[dict],
                           display_name: str) -> str:
    """A Stripe Terminal Location to register the reader at: reuse the shop's, else create one."""
    existing = (await db.execute(select(PaymentTerminal.provider_location).where(
        PaymentTerminal.org_id == org_id, PaymentTerminal.provider == "stripe",
        PaymentTerminal.provider_location.is_not(None)))).scalars().first()
    if existing:
        return existing
    a = {k: (v or "").strip() for k, v in (address or {}).items()}
    if not (a.get("line1") and a.get("city") and a.get("country")):
        raise TerminalError(422, "Enter the shop address (street, city, country) the reader is used at.")
    data = {"display_name": display_name[:100], "address[line1]": a["line1"], "address[city]": a["city"],
            "address[country]": a["country"].upper()[:2]}
    for k in ("postal_code", "state"):
        if a.get(k):
            data[f"address[{k}]"] = a[k]
    return (await _stripe(creds, "POST", "/v1/terminal/locations", data))["id"]


async def register_reader(db: AsyncSession, org_id: str, *, provider: str, label: str,
                          location_id: Optional[str] = None, registration_code: Optional[str] = None,
                          device_id: Optional[str] = None, address: Optional[dict] = None) -> PaymentTerminal:
    if provider not in PROVIDERS:
        raise TerminalError(422, "Choose Stripe, Square or the simulated reader.")
    label = (label or "").strip()[:120] or "Card reader"
    creds = await provider_creds(db, org_id, provider)
    test = payment_guard.test_mode(provider, creds)
    provider_location = device_type = None
    if provider == "stripe":
        code = (registration_code or "").strip() or (STRIPE_TEST_REGISTRATION_CODE if test else "")
        if not code:
            raise TerminalError(422, "Enter the registration code shown on the reader.")
        org = (await db.execute(select(Organisation).where(Organisation.id == org_id))).scalar_one()
        provider_location = await _stripe_location(db, org_id, creds, address, org.name or "Shop")
        reader = await _stripe(creds, "POST", "/v1/terminal/readers", {
            "registration_code": code, "label": label, "location": provider_location})
        reader_id, device_type = reader["id"], reader.get("device_type")
    elif provider == "square":
        reader_id = (device_id or "").strip()
        if not reader_id:
            raise TerminalError(422, "Choose the Square device.")
        if test and reader_id not in {d["id"] for d in SQUARE_SANDBOX_DEVICES}:
            raise TerminalError(422, "In the Square sandbox use one of the listed test devices.")
        device_type = "square_terminal"
    else:
        reader_id, device_type = f"sim_{uuid.uuid4().hex[:12]}", "simulated"

    t = (await db.execute(select(PaymentTerminal).where(
        PaymentTerminal.org_id == org_id, PaymentTerminal.provider == provider,
        PaymentTerminal.reader_id == reader_id))).scalar_one_or_none()
    if t and t.active:
        raise TerminalError(409, "That reader is already registered.")
    if not test and (await db.execute(select(PaymentTerminal.id).where(
            PaymentTerminal.provider == provider, PaymentTerminal.reader_id == reader_id,
            PaymentTerminal.org_id != org_id, PaymentTerminal.test_mode == False))).first():  # noqa: E712
        # Review P8: a real reader is one shop's (sandbox test devices are shared by design).
        raise TerminalError(409, "That reader is registered to another shop.")
    if not t:
        t = PaymentTerminal(org_id=org_id, provider=provider, reader_id=reader_id)
        db.add(t)
    t.label, t.location_id, t.provider_location = label, location_id or None, provider_location
    t.device_type, t.test_mode, t.active = device_type, test, True
    await db.commit()
    await db.refresh(t)
    return t


async def remove_reader(db: AsyncSession, t: PaymentTerminal) -> None:
    """Deactivate (charges keep pointing at it). Stripe is told too, best-effort."""
    pending = (await db.execute(select(TerminalCharge.id).where(
        TerminalCharge.terminal_id == t.id, TerminalCharge.status.in_(OPEN)))).first()
    if pending:
        raise TerminalError(409, "This reader has a charge waiting. Cancel it first.")
    if t.provider == "stripe":
        try:
            await _stripe(await provider_creds(db, t.org_id, "stripe"), "DELETE", f"/v1/terminal/readers/{t.reader_id}")
        except HTTPException as e:
            log.info("Stripe reader %s not deleted at Stripe: %s", t.reader_id, e.detail)
    t.active = False
    await db.commit()


# ── Charges ──────────────────────────────────────────────────────────────────
_STATUS_LABEL = {"pending": "Waiting for card", "processing": "Approved", "succeeded": "Approved",
                 "declined": "Declined", "canceled": "Canceled", "failed": "Failed"}


def ser_charge(c: TerminalCharge, terminal: Optional[PaymentTerminal] = None) -> dict:
    return {"id": c.id, "terminal_id": c.terminal_id, "terminal_label": terminal.label if terminal else None,
            "provider": c.provider, "provider_ref": c.provider_ref, "order_id": c.order_id,
            "sale_id": c.sale_id, "account_id": c.account_id, "amount": c.amount, "currency": c.currency,
            "status": c.status, "status_label": _STATUS_LABEL.get(c.status, c.status), "error": c.error,
            "payment_id": c.payment_id, "refunded_amount": c.refunded_amount or 0,
            "test_mode": terminal.test_mode if terminal else None,
            "created_at": c.created_at.isoformat() if c.created_at else None}


async def _org_currency(db: AsyncSession, org_id: str) -> str:
    org = (await db.execute(select(Organisation).where(Organisation.id == org_id))).scalar_one()
    return (org.currency_code or "usd").lower()


async def _pick_account(db: AsyncSession, org_id: str, account_id: Optional[str]) -> Account:
    if account_id:
        acc = (await db.execute(select(Account).where(
            Account.id == account_id, Account.org_id == org_id))).scalar_one_or_none()
        if not acc:
            raise TerminalError(404, "Account not found")
        return acc
    accs = (await db.execute(select(Account).where(
        Account.org_id == org_id, Account.active == True))).scalars().all()  # noqa: E712
    acc = next((a for a in accs if a.payment_option == "card"), None) or next(
        (a for a in accs if a.payment_option == "cashless"), None)
    if not acc:
        raise TerminalError(400, "Choose the account card payments go into.")
    return acc


async def start_charge(db: AsyncSession, *, org_id: str, actor_id: str, terminal_id: str,
                       amount: Optional[float], order_id: Optional[str] = None,
                       sale_id: Optional[str] = None, account_id: Optional[str] = None) -> TerminalCharge:
    if bool(order_id) == bool(sale_id):
        raise TerminalError(422, "Charge either a work order or a sale.")
    t = (await db.execute(select(PaymentTerminal).where(
        PaymentTerminal.id == terminal_id, PaymentTerminal.org_id == org_id,
        PaymentTerminal.active == True))).scalar_one_or_none()  # noqa: E712
    if not t:
        raise TerminalError(404, "Card reader not found")
    creds = await provider_creds(db, org_id, t.provider)
    acc = await _pick_account(db, org_id, account_id)

    if order_id:
        o = (await db.execute(select(Order).where(Order.id == order_id, Order.org_id == org_id))).scalar_one_or_none()
        if not o:
            raise TerminalError(404, "Order not found")
        from backend.services.order_money import balance_due
        owed = await balance_due(db, o)
        if amount is None:
            amount = owed
        if amount is None or amount <= 0:
            raise TerminalError(400, "Nothing to charge: this order is already settled." if owed <= 0
                                else "Amount must be positive")
        if amount > owed + 0.005:
            raise TerminalError(422, f"Amount is more than the {owed:.2f} still owed on this order.")
        what = f"Order {o.order_number}"
    else:
        s = (await db.execute(select(Sale).where(Sale.id == sale_id, Sale.org_id == org_id))).scalar_one_or_none()
        if not s:
            raise TerminalError(404, "Sale not found")
        if s.voided_at:
            raise TerminalError(409, "This sale was deleted.")
        if s.paid:
            raise TerminalError(409, "This sale is already paid.")
        amount = float(s.total or 0)   # a sale is always charged in full
        if amount <= 0:
            raise TerminalError(400, "Nothing to charge on this sale.")
        what = f"Sale {s.sale_number}"
    amount = q2(amount)

    # One charge at a time per reader, and per order/sale (a double tap must not send two).
    busy = (await db.execute(select(TerminalCharge).where(
        TerminalCharge.org_id == org_id, TerminalCharge.status.in_(OPEN),
        (TerminalCharge.terminal_id == t.id)
        | ((TerminalCharge.order_id == order_id) if order_id else (TerminalCharge.sale_id == sale_id))))).scalars().all()
    for b in busy:
        await refresh(db, b)
    if any(b.status in OPEN for b in busy):
        raise TerminalError(409, "A charge is already waiting on this reader or document. Cancel it first.")

    currency = await _org_currency(db, org_id)
    ch = TerminalCharge(org_id=org_id, terminal_id=t.id, provider=t.provider, order_id=order_id,
                        sale_id=sale_id, account_id=acc.id, amount=amount, currency=currency,
                        status="pending", created_by_id=actor_id)
    db.add(ch)
    await db.flush()
    try:
        if t.provider == "stripe":
            pi = await _stripe(creds, "POST", "/v1/payment_intents", {
                "amount": str(minor_units(amount, currency)), "currency": currency,
                "payment_method_types[]": "card_present", "capture_method": "automatic",
                "description": what, "metadata[charge_id]": ch.id, "metadata[org_id]": org_id,
            }, idempotency_key=f"tekly-charge-{ch.id}")
            ch.provider_ref = pi["id"]
            await db.commit()   # keep the PI id even if the reader call fails, so it can be canceled
            try:
                await _stripe(creds, "POST", f"/v1/terminal/readers/{t.reader_id}/process_payment_intent",
                              {"payment_intent": pi["id"]}, idempotency_key=f"tekly-process-{ch.id}")
            except HTTPException:
                try:
                    await _stripe(creds, "POST", f"/v1/payment_intents/{pi['id']}/cancel")
                except HTTPException:
                    pass
                raise
        elif t.provider == "square":
            body = await _square(creds, "POST", "/v2/terminals/checkouts", {
                "idempotency_key": ch.id,
                "checkout": {"amount_money": {"amount": minor_units(amount, currency), "currency": currency.upper()},
                             "device_options": {"device_id": t.reader_id},
                             "reference_id": ch.id, "note": what[:250]}})
            ch.provider_ref = (body.get("checkout") or {}).get("id")
        else:
            ch.provider_ref = f"simchg_{ch.id[:12]}"
    except HTTPException as e:
        ch.status, ch.error = "failed", str(e.detail)[:500]
        await db.commit()
        raise
    await db.commit()
    await db.refresh(ch)
    return ch


async def refresh(db: AsyncSession, ch: TerminalCharge) -> TerminalCharge:
    """Poll the provider for a waiting charge and settle it if it has finished. A provider that's
    down leaves the charge waiting; the next poll (or the webhook) tries again."""
    if ch.status not in OPEN or not ch.provider_ref or ch.provider == "simulated":
        return ch
    try:
        creds = await provider_creds(db, ch.org_id, ch.provider)
        if ch.provider == "stripe":
            pi = await _stripe(creds, "GET", f"/v1/payment_intents/{ch.provider_ref}")
            return await apply_stripe_intent(db, ch, pi)
        co = (await _square(creds, "GET", f"/v2/terminals/checkouts/{ch.provider_ref}")).get("checkout") or {}
        return await apply_square_checkout(db, ch, co)
    except HTTPException as e:
        log.info("status poll for charge %s failed: %s", ch.id, e.detail)
        return ch


async def apply_stripe_intent(db: AsyncSession, ch: TerminalCharge, pi: dict) -> TerminalCharge:
    status = pi.get("status")
    if status == "succeeded":
        captured = await _checked_capture(db, ch, pi.get("amount_received") or pi.get("amount"), pi.get("currency"))
        if captured is None:
            return ch
        return await settle_succeeded(db, ch, captured=captured)
    if status == "canceled":
        return await settle_failed(db, ch, "canceled", "Canceled")
    err = pi.get("last_payment_error") or {}
    if status == "requires_payment_method" and err:
        await settle_failed(db, ch, "declined", err.get("message") or "Card declined")
        if ch.status == "declined":
            # Stop the old intent from being collected later; a retry makes a new charge.
            try:
                await _stripe(await provider_creds(db, ch.org_id, "stripe"), "POST",
                              f"/v1/payment_intents/{ch.provider_ref}/cancel")
            except HTTPException:
                pass
    return ch


async def apply_square_checkout(db: AsyncSession, ch: TerminalCharge, co: dict) -> TerminalCharge:
    status = (co.get("status") or "").upper()
    if status == "COMPLETED":
        pay_id = (co.get("payment_ids") or [None])[0]
        money = co.get("amount_money") or {}
        if pay_id:
            # The checkout carries the asked amount; the payment carries what was taken (tips included).
            try:
                p = (await _square(await provider_creds(db, ch.org_id, "square"), "GET", f"/v2/payments/{pay_id}")).get("payment") or {}
                money = p.get("total_money") or p.get("amount_money") or money
            except HTTPException as e:
                log.info("Square payment %s not fetched (%s); using the checkout amount", pay_id, e.detail)
        captured = await _checked_capture(db, ch, money.get("amount"), money.get("currency"))
        if captured is None:
            return ch
        return await settle_succeeded(db, ch, provider_payment_id=pay_id, captured=captured)
    if status == "CANCELED":
        reason = (co.get("cancel_reason") or "").upper()
        msg = {"BUYER_CANCELED": "The customer canceled on the reader",
               "TIMED_OUT": "The reader timed out"}.get(reason, "Canceled")
        return await settle_failed(db, ch, "canceled", msg)
    return ch


def _from_minor(minor: int, currency: str) -> float:
    c = (currency or "").lower()
    exp = 0 if c in ZERO_DECIMAL else 3 if c in THREE_DECIMAL else 2
    return q2(int(minor) / (10 ** exp))


async def _checked_capture(db: AsyncSession, ch: TerminalCharge, minor, currency) -> Optional[float]:
    """What the provider says it took, as a document amount. A tip makes it more than asked, and
    that extra is real money in the account. A different currency or a smaller amount is never
    booked: the charge is marked failed for staff to check in the provider's dashboard (it would
    otherwise sit "waiting" forever and block the reader)."""
    if minor is None:
        return ch.amount
    if (currency and currency.lower() != (ch.currency or "").lower()) or int(minor) < minor_units(ch.amount, ch.currency):
        log.warning("charge %s: provider reported %s %s, expected %s %s", ch.id, minor, currency, ch.amount, ch.currency)
        await settle_failed(db, ch, "failed", "The provider reported a different amount or currency. "
                                              "Check this payment in the provider's dashboard.")
        return None
    return _from_minor(minor, ch.currency)


async def settle_failed(db: AsyncSession, ch: TerminalCharge, status: str, error: str) -> TerminalCharge:
    res = await db.execute(sa_update(TerminalCharge)
                           .where(TerminalCharge.id == ch.id, TerminalCharge.status.in_(OPEN))
                           .values(status=status, error=error[:500], updated_at=now_utc())
                           .execution_options(synchronize_session=False))
    await db.commit()
    await db.refresh(ch)
    if res.rowcount:
        await _log(db, ch, f"Card reader charge {ch.amount:.2f} {status}: {error}")
    return ch


async def _log(db: AsyncSession, ch: TerminalCharge, msg: str) -> None:
    from backend.routers.feed import log_system_event
    await log_system_event(db, ch.org_id, "order" if ch.order_id else "sale", ch.order_id or ch.sale_id, msg)
    await db.commit()


async def settle_succeeded(db: AsyncSession, ch: TerminalCharge, *, provider_payment_id: Optional[str] = None,
                           captured: Optional[float] = None) -> TerminalCharge:
    """Record the captured money exactly once. The webhook, the status poll and a redelivered
    event can all land together; the conditional UPDATE lets exactly one of them post."""
    prior = ch.status
    claimed = await db.execute(sa_update(TerminalCharge)
        .where(TerminalCharge.id == ch.id, TerminalCharge.payment_id.is_(None),
               TerminalCharge.status.not_in(("processing", "succeeded")))
        .values(status="processing").execution_options(synchronize_session=False))
    if claimed.rowcount == 0:
        await db.refresh(ch)
        return ch
    tip = q2(captured - ch.amount) if captured is not None and captured > ch.amount else 0.0
    if tip:
        ch.amount = q2(captured)   # book what was actually taken; refunds are capped by it too
    tip_note = f" (includes a {tip:.2f} tip)" if tip else ""
    acc = (await db.execute(select(Account).where(
        Account.id == ch.account_id, Account.org_id == ch.org_id))).scalar_one_or_none()
    t = (await db.execute(select(PaymentTerminal).where(PaymentTerminal.id == ch.terminal_id))).scalar_one()
    from backend.routers.cash import post_transaction
    from backend.routers.feed import log_system_event
    note = ""
    if ch.order_id:
        o = (await db.execute(select(Order).where(
            Order.id == ch.order_id, Order.org_id == ch.org_id).with_for_update())).scalar_one()
        # Review M4b: the card may be approved after the order was paid another way (cash, a pay
        # link, a credit note). The money was taken, so it is recorded, but staff must look.
        from backend.services.order_money import balance_due
        owed = await balance_due(db, o)
        if ch.amount > owed + 0.005:
            note = (f" This is {q2(ch.amount - owed):.2f} more than was still owed ({owed:.2f}): "
                    "check for a double payment and refund if needed.")
        # Same poster as the Take payment dialog (routers/cash.post_order_payment). Captured money
        # is always recorded, so the backdating rule (a staff-entry check) doesn't apply here.
        from backend.routers.cash import post_order_payment
        acc = acc or await _pick_account(db, ch.org_id, None)
        pay, _fee = await post_order_payment(
            db, ch.org_id, o, ch.amount, acc, "card", actor_id=ch.created_by_id,
            reference=f"{t.label} ({ch.provider})"[:200], provider_ref=ch.provider_ref,
            comment=f"Card reader payment for {o.order_number}", check_backdating=False)
        ch.payment_id = pay.id
        if prior in FINAL_FAILED:
            note += " It had been marked as " + prior + ": check for a double payment."
        await log_system_event(db, ch.org_id, "order", o.id,
                               f"Card reader payment {ch.amount:.2f}{tip_note} on {t.label}.{note}")
    else:
        s = (await db.execute(select(Sale).where(
            Sale.id == ch.sale_id, Sale.org_id == ch.org_id).with_for_update())).scalar_one()
        if s.voided_at:
            # Review M6: the sale was deleted while the card was on the reader. Its books are
            # already reversed, so nothing is posted; the money must go back to the card.
            note = " The sale had already been deleted, so nothing was booked: refund this card payment."
        elif s.paid or prior in FINAL_FAILED:
            note = " Check for a double payment and refund if needed."
        if acc and not s.voided_at:
            await post_transaction(db, ch.org_id, acc.id, "income", abs(ch.amount),
                                   comment=f"Sale {s.sale_number} (card reader)", ref_type="sale",
                                   ref_id=s.id, employee_id=ch.created_by_id)
            await _post_fee(db, ch, acc, "sale", s.id, s.sale_number)
            s.account_id = s.account_id or acc.id
        if not s.voided_at:
            s.paid = True
        await log_system_event(db, ch.org_id, "sale", s.id,
                               f"Card reader payment {ch.amount:.2f}{tip_note} on {t.label}.{note}")
    ch.status = "succeeded"
    ch.error = None
    ch.provider_payment_id = provider_payment_id or ch.provider_payment_id
    ch.updated_at = now_utc()
    await db.commit()
    await db.refresh(ch)
    if ch.order_id:
        try:
            from backend.services import notification_rules as _nr
            await _nr.evaluate(db, org_id=ch.org_id, order_id=ch.order_id, event="payment_received")
            await _nr.notify_staff_order(db, org_id=ch.org_id, order_id=ch.order_id,
                                         event="payment_received", actor_id=ch.created_by_id)
        except Exception:  # noqa: BLE001 — notifications are best-effort
            log.exception("payment_received notification failed for charge %s", ch.id)
    return ch


async def _post_fee(db, ch, acc, ref_type, ref_id, number) -> None:
    if not (acc.fee_pct or acc.fee_fixed):
        return
    from backend.routers.cash import post_transaction
    fee = q2(abs(ch.amount) * (acc.fee_pct or 0) / 100 + (acc.fee_fixed or 0))
    if fee <= 0:
        return
    item = (await db.execute(select(CashFlowItem).where(
        CashFlowItem.org_id == ch.org_id, CashFlowItem.name == "Transaction fees"))).scalar_one_or_none()
    await post_transaction(db, ch.org_id, acc.id, "fee", -fee, cash_flow_item_id=item.id if item else None,
                           comment=f"Processing fee for {number}", ref_type=ref_type, ref_id=ref_id,
                           employee_id=ch.created_by_id)


async def cancel(db: AsyncSession, ch: TerminalCharge) -> TerminalCharge:
    if ch.status not in OPEN:
        raise TerminalError(409, "Only a waiting charge can be canceled.")
    t = (await db.execute(select(PaymentTerminal).where(PaymentTerminal.id == ch.terminal_id))).scalar_one()
    if ch.provider == "stripe":
        creds = await provider_creds(db, ch.org_id, "stripe")
        try:
            await _stripe(creds, "POST", f"/v1/terminal/readers/{t.reader_id}/cancel_action")
        except HTTPException:
            pass   # nothing on the reader any more; canceling the intent below is what matters
        if ch.provider_ref:
            try:
                await _stripe(creds, "POST", f"/v1/payment_intents/{ch.provider_ref}/cancel")
            except HTTPException:
                # Most likely it was paid a moment ago: settle that instead of claiming "canceled".
                await refresh(db, ch)
                if ch.status not in OPEN:
                    return ch
                raise
    elif ch.provider == "square" and ch.provider_ref:
        creds = await provider_creds(db, ch.org_id, "square")
        try:
            await _square(creds, "POST", f"/v2/terminals/checkouts/{ch.provider_ref}/cancel")
        except HTTPException:
            await refresh(db, ch)
            if ch.status not in OPEN:
                return ch
            raise
    return await settle_failed(db, ch, "canceled", "Canceled by staff")


async def simulate(db: AsyncSession, ch: TerminalCharge, outcome: str) -> TerminalCharge:
    """QA helper (local development and tests only): present a test card on the reader."""
    if settings.is_deployed:   # review S5: same gate as the rest of the simulated flow
        raise TerminalError(403, "Test cards can only be presented on a local development server.")
    if outcome not in ("approve", "decline"):
        raise TerminalError(422, "Outcome must be approve or decline.")
    if ch.status not in OPEN:
        raise TerminalError(409, "This charge is no longer waiting for a card.")
    if ch.provider == "simulated":
        if outcome == "approve":
            return await settle_succeeded(db, ch)
        return await settle_failed(db, ch, "declined", "Card declined (test card)")
    if ch.provider == "stripe":
        creds = await provider_creds(db, ch.org_id, "stripe")
        if payment_guard.is_live_stripe_key(creds.get("secret_key")):
            raise TerminalError(403, "Test cards only work with Stripe test keys.")
        t = (await db.execute(select(PaymentTerminal).where(PaymentTerminal.id == ch.terminal_id))).scalar_one()
        await _stripe(creds, "POST", f"/v1/test_helpers/terminal/readers/{t.reader_id}/present_payment_method",
                      {"type": "card_present", "card_present[number]": STRIPE_TEST_CARDS[outcome]})
        return await refresh(db, ch)
    raise TerminalError(400, "Square sandbox devices decide the outcome themselves: pick the test device that "
                             "approves or cancels when you add the reader.")


async def refund(db: AsyncSession, ch: TerminalCharge, *, amount: Optional[float], actor_id: str) -> TerminalCharge:
    if ch.status != "succeeded":
        raise TerminalError(409, "Only an approved charge can be refunded.")
    left = q2((ch.amount or 0) - (ch.refunded_amount or 0))
    amount = q2(left if amount is None else amount)
    if amount <= 0:
        raise TerminalError(400, "Refund amount must be positive")
    if amount > left + 0.005:
        raise TerminalError(422, f"Only {left:.2f} is left to refund on this charge.")
    # Claim the refund before calling the provider so two clicks can't both refund.
    claimed = await db.execute(sa_update(TerminalCharge)
        .where(TerminalCharge.id == ch.id, TerminalCharge.refunded_amount == ch.refunded_amount)
        .values(refunded_amount=q2((ch.refunded_amount or 0) + amount))
        .execution_options(synchronize_session=False))
    if claimed.rowcount == 0:
        raise TerminalError(409, "Another refund is in progress. Refresh and try again.")
    await db.flush()
    idem = f"tekly-refund-{ch.id}-{minor_units(ch.refunded_amount or 0, ch.currency)}"
    try:
        if ch.provider == "stripe":
            creds = await provider_creds(db, ch.org_id, "stripe")
            r = await _stripe(creds, "POST", "/v1/refunds", {
                "payment_intent": ch.provider_ref, "amount": str(minor_units(amount, ch.currency))},
                idempotency_key=idem)
            ref = r.get("id")
        elif ch.provider == "square":
            creds = await provider_creds(db, ch.org_id, "square")
            if not ch.provider_payment_id:
                raise TerminalError(409, "Square hasn't reported the payment id yet. Try again shortly.")
            r = await _square(creds, "POST", "/v2/refunds", {
                "idempotency_key": idem, "payment_id": ch.provider_payment_id,
                "amount_money": {"amount": minor_units(amount, ch.currency), "currency": ch.currency.upper()}})
            ref = (r.get("refund") or {}).get("id")
        else:
            ref = f"simref_{uuid.uuid4().hex[:10]}"
    except HTTPException:
        await db.rollback()
        raise
    await _post_refund(db, ch, amount, ref, actor_id)
    await db.commit()
    await db.refresh(ch)
    return ch


async def _post_refund(db: AsyncSession, ch: TerminalCharge, amount: float, ref: Optional[str], actor_id: str) -> None:
    from backend.routers.cash import post_transaction
    from backend.routers.feed import log_system_event
    acc_id = ch.account_id
    if ch.order_id:
        o = (await db.execute(select(Order).where(Order.id == ch.order_id))).scalar_one()
        if acc_id:
            await post_transaction(db, ch.org_id, acc_id, "expense", -abs(amount),
                                   comment=f"Card refund {o.order_number}", ref_type="order",
                                   ref_id=o.id, employee_id=actor_id)
        rp = Payment(order_id=o.id, client_id=o.client_id, method="card", kind="refund", account_id=acc_id,
                     amount=amount, received_by_id=actor_id, reference="Card reader refund", provider_ref=ref)
        db.add(rp)
        await db.flush()
        if ch.payment_id:
            inv_id = (await db.execute(select(Payment.invoice_id).where(Payment.id == ch.payment_id))).scalar()
            inv = (await db.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one_or_none() if inv_id else None
            if inv:
                inv.amount_paid = q2(max((inv.amount_paid or 0) - amount, 0))
                from backend.services.invoicing import settle_status
                settle_status(inv)
        if o.client_id:
            from backend.services.ledger import post_contact_ledger
            await post_contact_ledger(db, ch.org_id, o.client_id, "refund", abs(amount),
                                      note=f"Card refund for {o.order_number}", ref_type="payment",
                                      ref_id=rp.id, created_by_id=actor_id)
        await log_system_event(db, ch.org_id, "order", o.id, f"Card reader refund {amount:.2f}")
    else:
        s = (await db.execute(select(Sale).where(Sale.id == ch.sale_id))).scalar_one()
        if s.voided_at:
            # A deleted sale's books are reversed and a charge settled afterwards booked nothing
            # (delete_sale refuses while card money is unrefunded or a charge is open), so only the
            # card is refunded here (review M6).
            await log_system_event(db, ch.org_id, "sale", s.id,
                                   f"Card reader refund {amount:.2f} (sale was deleted; nothing to reverse)")
            return
        if acc_id:
            await post_transaction(db, ch.org_id, acc_id, "expense", -abs(amount),
                                   comment=f"Card refund sale {s.sale_number}", ref_type="sale",
                                   ref_id=s.id, employee_id=actor_id)
        await log_system_event(db, ch.org_id, "sale", s.id, f"Card reader refund {amount:.2f}")


async def open_sale_charge(db: AsyncSession, sale_id: str) -> bool:
    """A reader charge for this sale is still waiting for the card (or being recorded)."""
    return (await db.execute(select(TerminalCharge.id).where(
        TerminalCharge.sale_id == sale_id,
        TerminalCharge.status.in_(OPEN + ("processing",))))).first() is not None


async def unrefunded_sale_card_payment(db: AsyncSession, sale_id: str) -> float:
    """Card money taken on a reader for this sale and not yet given back."""
    rows = (await db.execute(select(TerminalCharge.amount, TerminalCharge.refunded_amount).where(
        TerminalCharge.sale_id == sale_id, TerminalCharge.status == "succeeded"))).all()
    return q2(sum((a or 0) - (r or 0) for a, r in rows))


# ── Webhooks ─────────────────────────────────────────────────────────────────
async def find_charge(db: AsyncSession, provider: str, provider_ref: Optional[str],
                      org_id: Optional[str] = None) -> Optional[TerminalCharge]:
    if not provider_ref:
        return None
    q = select(TerminalCharge).where(TerminalCharge.provider == provider, TerminalCharge.provider_ref == provider_ref)
    if org_id:
        q = q.where(TerminalCharge.org_id == org_id)
    return (await db.execute(q)).scalar_one_or_none()


async def claim_event(db: AsyncSession, provider: str, event_id: str, org_id: str) -> bool:
    """Record a provider event id. False when it was already applied (a redelivery)."""
    from sqlalchemy.exc import IntegrityError

    from backend.db.database import insert_ignore
    from backend.models import PaymentWebhookEvent
    try:
        inserted = await insert_ignore(db, PaymentWebhookEvent, id=str(uuid.uuid4()), provider=provider,
                                       event_id=event_id[:255], org_id=org_id, created_at=now_utc())
    except IntegrityError:
        await db.rollback()
        return False
    return bool(inserted)
