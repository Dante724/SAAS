"""Payment gateway seam (Phase 2A).

A PaymentIntent is created for an order/invoice and the customer is sent to a
hosted checkout. On success (Stripe webhook, or the simulated confirm endpoint)
the intent is reconciled into the existing cash system exactly like a manual
payment: a Transaction is posted to the target Account and a Payment row is
written against the invoice.

When STRIPE_SECRET_KEY is set the StripeGateway creates a real Checkout Session;
otherwise the SimulatedGateway returns a local confirm URL so the whole flow is
testable end-to-end without keys. Real charges go live the moment the key is set.
"""
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models import Account, Invoice, Order, Payment, PaymentIntent
from backend.services.numbering import next_number
from backend.services.money import minor_units, q2

log = logging.getLogger("tekly.payments")


# ── Gateways ─────────────────────────────────────────────────────────────────
class PaymentGateway:
    name = "base"

    def __init__(self, creds: Optional[dict] = None):
        # 144jn: per-shop credentials (Stripe/Square/SumUp keys of THIS org).
        self.creds = creds or {}

    async def create_checkout(self, *, intent: PaymentIntent, description: str) -> dict:
        raise NotImplementedError


class SimulatedGateway(PaymentGateway):
    name = "simulated"

    async def create_checkout(self, *, intent: PaymentIntent, description: str) -> dict:
        # No external call: hand back a local URL that the test/dev confirms.
        ref = f"sim_{intent.id[:12]}"
        return {"provider_ref": ref,
                "checkout_url": f"/api/payments/intents/{intent.id}/simulate-pay",
                "status": "pending"}


def _return_url(configured: str, intent: PaymentIntent) -> str:
    """Where a hosted checkout sends the customer back. A shop's or operator's own URL gets
    ?intent=…; by default it's the customer's pay page, which confirms with the provider."""
    if configured:
        return configured + ("&" if "?" in configured else "?") + "intent=" + intent.id
    return f"{settings.public_base}/pay/{intent.id}"


class StripeGateway(PaymentGateway):
    name = "stripe"

    async def create_checkout(self, *, intent: PaymentIntent, description: str) -> dict:
        # Stripe's SDK is sync; call its REST API directly via httpx to stay async
        # and avoid a hard dependency. Amounts are in the smallest currency unit.
        import httpx
        amount_minor = minor_units(intent.amount, intent.currency)
        c = self.creds
        data = {
            "mode": "payment",
            "success_url": _return_url(c.get("success_url") or settings.STRIPE_SUCCESS_URL, intent),
            "cancel_url": _return_url(c.get("cancel_url") or settings.STRIPE_CANCEL_URL, intent),
            "client_reference_id": intent.id,
            "line_items[0][quantity]": "1",
            "line_items[0][price_data][currency]": intent.currency,
            "line_items[0][price_data][unit_amount]": str(amount_minor),
            "line_items[0][price_data][product_data][name]": description[:120] or "Payment",
            "metadata[intent_id]": intent.id,
            "metadata[org_id]": intent.org_id,
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                "https://api.stripe.com/v1/checkout/sessions",
                data=data, auth=(c.get("secret_key") or settings.STRIPE_SECRET_KEY, ""))
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"Stripe {resp.status_code}: {resp.text[:200]}")
        body = resp.json()
        return {"provider_ref": body.get("id"), "checkout_url": body.get("url"), "status": "pending"}


class SquareGateway(PaymentGateway):
    """Square Payment Links (quick pay). provider_ref stores the Square ORDER id
    so the payment webhook (which carries order_id) can find the intent."""
    name = "square"

    @staticmethod
    def base_url(env: str | None = None) -> str:
        env = env or settings.SQUARE_ENV
        return ("https://connect.squareup.com" if env == "production"
                else "https://connect.squareupsandbox.com")

    async def create_checkout(self, *, intent: PaymentIntent, description: str) -> dict:
        import httpx
        c = self.creds
        amount_minor = minor_units(intent.amount, intent.currency)
        payload = {
            "idempotency_key": intent.id,
            "quick_pay": {
                "name": (description or "Payment")[:120],
                "price_money": {"amount": amount_minor, "currency": intent.currency.upper()},
                "location_id": c.get("location_id") or settings.SQUARE_LOCATION_ID,
            },
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{self.base_url(c.get('env'))}/v2/online-checkout/payment-links",
                json=payload,
                headers={"Authorization": f"Bearer {c.get('access_token') or settings.SQUARE_ACCESS_TOKEN}",
                         "Square-Version": "2024-06-04"})
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"Square {resp.status_code}: {resp.text[:200]}")
        link = resp.json().get("payment_link") or {}
        return {"provider_ref": link.get("order_id") or link.get("id"),
                "provider_link_id": link.get("id"),     # needed to delete the link later
                "checkout_url": link.get("url"), "status": "pending"}


class SumUpGateway(PaymentGateway):
    """SumUp hosted checkout. provider_ref stores the checkout id, which the
    refresh poll uses to confirm payment (SumUp has no signed webhook here)."""
    name = "sumup"

    async def create_checkout(self, *, intent: PaymentIntent, description: str) -> dict:
        import httpx
        c = self.creds
        payload = {
            "checkout_reference": intent.id,
            "amount": q2(intent.amount),
            "currency": intent.currency.upper(),
            "merchant_code": c.get("merchant_code") or settings.SUMUP_MERCHANT_CODE,
            "description": (description or "Payment")[:120],
            "hosted_checkout": {"enabled": True},
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post("https://api.sumup.com/v0.1/checkouts", json=payload,
                                     headers={"Authorization": f"Bearer {c.get('api_key') or settings.SUMUP_API_KEY}"})
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"SumUp {resp.status_code}: {resp.text[:200]}")
        body = resp.json()
        url = (body.get("hosted_checkout_url")
               or (body.get("hosted_checkout") or {}).get("url"))
        return {"provider_ref": body.get("id"), "checkout_url": url, "status": "pending"}


PROVIDERS = ("stripe", "square", "sumup", "simulated")


_GW_CLASSES = {"stripe": StripeGateway, "square": SquareGateway,
               "sumup": SumUpGateway, "simulated": SimulatedGateway}


def _configured(provider: str, creds: dict) -> bool:
    if provider == "stripe":
        return bool(creds.get("secret_key"))
    if provider == "square":
        return bool(creds.get("access_token") and creds.get("location_id"))
    if provider == "sumup":
        return bool(creds.get("api_key") and creds.get("merchant_code"))
    return provider == "simulated"


async def _all_creds(db: AsyncSession, org_id: str) -> dict:
    """Effective (per-shop, else env) creds for every keyed gateway."""
    from backend.services import org_creds
    return {p: await org_creds.effective(db, org_id, p) for p in ("stripe", "square", "sumup")}


async def available_gateways_org(db: AsyncSession, org_id: str) -> dict:
    creds = await _all_creds(db, org_id)
    return {"stripe": _configured("stripe", creds["stripe"]),
            "square": _configured("square", creds["square"]),
            "sumup": _configured("sumup", creds["sumup"]),
            "simulated": True}


async def resolve_gateway(db: AsyncSession, org_id: str, provider: str | None = None) -> PaymentGateway:
    """Pick THIS shop's gateway, built with THIS shop's own credentials."""
    creds = await _all_creds(db, org_id)
    if provider and provider != "auto":
        cls = _GW_CLASSES.get(provider)
        if not cls:
            raise RuntimeError(f"Unknown payment provider '{provider}'")
        if provider != "simulated" and not _configured(provider, creds.get(provider, {})):
            raise RuntimeError(f"Payment provider '{provider}' is not configured for this shop")
        if provider == "simulated" and settings.is_deployed:
            raise RuntimeError("The simulated payment provider is for testing only. "
                               "Connect Stripe, Square or SumUp in Settings › Payments.")
        return cls(creds.get(provider, {}))
    for name in ("stripe", "square", "sumup"):
        if _configured(name, creds[name]):
            return _GW_CLASSES[name](creds[name])
    if settings.is_deployed:
        # A real deploy never hands customers a simulated checkout: it 403s and can't be paid.
        raise RuntimeError("No payment provider is set up for this shop. "
                           "Connect Stripe, Square or SumUp in Settings › Payments.")
    return SimulatedGateway()


async def gateway_name_org(db: AsyncSession, org_id: str, provider: str | None = None) -> str:
    try:
        return (await resolve_gateway(db, org_id, provider)).name
    except RuntimeError:
        return "unconfigured"


async def org_provider(db: AsyncSession, org_id: str) -> str:
    from backend.models import Organisation
    org = (await db.execute(select(Organisation).where(Organisation.id == org_id))).scalar_one_or_none()
    return (org.payment_provider if org else None) or "auto"


# ── Intent creation ──────────────────────────────────────────────────────────
async def create_intent(db: AsyncSession, *, org_id: str, order_id: Optional[str],
                        amount: float, account_id: Optional[str],
                        currency: Optional[str] = None) -> PaymentIntent:
    # Stored money is always in the org's base currency, so that's what a checkout must charge.
    # Hard-coding "usd" turned an INR 5,000 balance into a $5,000 checkout.
    if not currency:
        from backend.models import Organisation
        org = (await db.execute(select(Organisation).where(Organisation.id == org_id))).scalar_one_or_none()
        currency = ((org.currency_code if org else None) or "usd")
    currency = currency.lower()
    invoice_id = None
    description = "Tekly payment"
    if order_id:
        o = (await db.execute(select(Order).where(
            Order.id == order_id, Order.org_id == org_id))).scalar_one_or_none()
        if not o:
            from fastapi import HTTPException
            raise HTTPException(404, "Order not found")
        description = f"Order {o.order_number}"
        from backend.services.order_money import primary_invoice
        inv = await primary_invoice(db, order_id)
        invoice_id = inv.id if inv else None
    gw = await resolve_gateway(db, org_id, await org_provider(db, org_id))
    intent = PaymentIntent(
        org_id=org_id, order_id=order_id, invoice_id=invoice_id, account_id=account_id,
        provider=gw.name, amount=q2(amount), currency=currency, status="created")
    db.add(intent)
    await db.flush()
    import httpx
    try:
        res = await gw.create_checkout(intent=intent, description=description)
    except httpx.HTTPError as e:
        # A timeout or dropped connection used to escape as a 500; the caller turns this into a 502.
        log.warning("checkout with %s failed: %s", gw.name, type(e).__name__)
        raise RuntimeError(f"Couldn't reach {gw.name.title()} right now. Try again in a minute.") from e
    intent.provider_ref = res["provider_ref"]
    intent.provider_link_id = res.get("provider_link_id")
    intent.checkout_url = res["checkout_url"]
    intent.status = res["status"]
    await db.commit()
    await db.refresh(intent)
    return intent


# ── Reconciliation (shared by Stripe webhook + simulated confirm) ────────────
async def mark_succeeded(db: AsyncSession, intent: PaymentIntent) -> PaymentIntent:
    """Idempotently reconcile a succeeded intent into the cash system."""
    if intent.status == "succeeded" and intent.payment_id:
        return intent  # already reconciled
    # Claim the intent atomically before posting anything: the Stripe webhook, Square webhook and
    # the public /refresh can all land concurrently, and a plain status check let two of them both
    # post a Transaction + Payment (double amount_paid). The conditional UPDATE row-locks on
    # Postgres, so the loser re-evaluates "payment_id IS NULL" after the winner commits.
    was_canceled = intent.status == "canceled"
    from sqlalchemy import update as _upd
    claimed = await db.execute(_upd(PaymentIntent)
        .where(PaymentIntent.id == intent.id, PaymentIntent.payment_id.is_(None),
               PaymentIntent.status != "processing")
        .values(status="processing").execution_options(synchronize_session=False))
    if claimed.rowcount == 0:
        await db.refresh(intent)
        return intent

    account = None
    if intent.account_id:
        account = (await db.execute(select(Account).where(
            Account.id == intent.account_id, Account.org_id == intent.org_id))).scalar_one_or_none()
    if not account:
        account = (await db.execute(select(Account).where(
            Account.org_id == intent.org_id, Account.active == True)  # noqa: E712
            .order_by(Account.is_company.desc()))).scalars().first()

    inv = None
    if intent.invoice_id:
        inv = (await db.execute(select(Invoice).where(Invoice.id == intent.invoice_id))).scalar_one_or_none()
    if not inv and intent.order_id:
        from backend.services.order_money import primary_invoice
        inv = await primary_invoice(db, intent.order_id)
        if not inv:
            o = (await db.execute(select(Order).where(Order.id == intent.order_id))).scalar_one_or_none()
            inv = Invoice(invoice_number=await next_number(db, intent.org_id, "invoice"), org_id=intent.org_id,
                          order_id=intent.order_id, client_id=o.client_id if o else None,
                          status="draft", subtotal=(o.total if o else intent.amount) or 0,
                          total=(o.total if o else intent.amount) or 0, issued_at=datetime.utcnow())
            db.add(inv)
            await db.flush()
            intent.invoice_id = inv.id

    # post income to the cashbox (reuse the canonical transaction poster)
    if account:
        from backend.models import CashFlowItem
        from backend.routers.cash import post_transaction
        sales = (await db.execute(select(CashFlowItem).where(
            CashFlowItem.org_id == intent.org_id, CashFlowItem.name == "Sales"))).scalar_one_or_none()
        # The provider has already captured this money, so it is never refused here. A till held
        # in another currency is a setup mistake that staff paths now reject; flag it loudly.
        from backend.routers.cash import _org_base_code
        base = await _org_base_code(db, intent.org_id)
        if account.currency_code and account.currency_code.upper() != base.upper():
            log.warning("online payment %s posted to %s account %s (org base %s) — check the till",
                        intent.id, account.currency_code, account.id, base)
        await post_transaction(db, intent.org_id, account.id, "invoice_payment", abs(intent.amount),
                               cash_flow_item_id=sales.id if sales else None,
                               comment=f"Online payment ({intent.provider})",
                               ref_type="order", ref_id=intent.order_id, native_amount=True)

    pay = Payment(invoice_id=inv.id if inv else None, order_id=intent.order_id,
                  client_id=inv.client_id if inv else None, method="online",
                  kind="payment", account_id=account.id if account else None,
                  amount=intent.amount, reference=f"{intent.provider}:{intent.provider_ref}")
    db.add(pay)
    await db.flush()
    if inv:
        inv.amount_paid = q2((inv.amount_paid or 0) + intent.amount)   # quantize before comparing
        from backend.services.invoicing import settle_status
        settle_status(inv)
    intent.payment_id = pay.id
    intent.status = "succeeded"
    intent.updated_at = datetime.utcnow()
    # The client paid online: reflect it on their balance like every in-store payment path does
    # (it was never posted, so the client kept showing the full amount owed).
    _client_id = inv.client_id if inv else None
    if _client_id:
        from backend.services.ledger import post_contact_ledger
        await post_contact_ledger(db, intent.org_id, _client_id, "income", -abs(intent.amount),
                                  note=f"Online payment ({intent.provider})", ref_type="payment",
                                  ref_id=pay.id)
    if intent.order_id:
        _ord = (await db.execute(select(Order).where(Order.id == intent.order_id))).scalar_one_or_none()
        if _ord:
            from backend.services.order_money import cancel_open_pay_links
            await db.flush()
            await cancel_open_pay_links(db, _ord)

    # The provider took money on a link we had already revoked (the customer paid an old link,
    # possibly on top of a newer one). It's recorded, since it was captured, but staff must look.
    if was_canceled and intent.order_id:
        log.warning("payment %s captured on revoked pay link %s", pay.id, intent.id)
        try:
            from backend.routers.feed import log_system_event
            await log_system_event(db, intent.org_id, "order", intent.order_id,
                                   f"Paid on a cancelled payment link ({intent.provider}, {intent.amount:.2f}). "
                                   "Check for a double payment and refund if needed.")
        except Exception:
            log.exception("could not flag payment %s on revoked link", pay.id)

    # Build 144w: workflow automations for payment_received (best-effort).
    if intent.order_id:
        try:
            from backend.services import automation as _auto
            _o = (await db.execute(select(Order).where(Order.id == intent.order_id))).scalar_one_or_none()
            if _o:
                await _auto.run(db, intent.org_id, "payment_received", order=_o)
        except Exception:
            pass

    # Build 144z: reflect the money in QuickBooks (no-op unless QBO is connected).
    # Push in dependency order so the invoice's customer and the invoice exist first.
    try:
        from backend.services import qbo as _qbo
        await _qbo.autosync(db, intent.org_id, [
            ("contact", inv.client_id if inv else None),
            ("invoice", inv.id if inv else None),
            ("payment", pay.id)])
    except Exception:
        pass

    # Loyalty: earn points for the paying client (program on when earn_rate > 0)
    try:
        from backend.models import Client, LoyaltyLedger, Organisation
        org = (await db.execute(select(Organisation).where(Organisation.id == intent.org_id))).scalar_one_or_none()
        client_id = inv.client_id if inv else None
        if org and org.loyalty_earn_rate > 0 and client_id:
            pts = q2(intent.amount * org.loyalty_earn_rate)
            db.add(LoyaltyLedger(org_id=intent.org_id, client_id=client_id, points=pts,
                                 ref_type="payment", ref_id=pay.id,
                                 note=f"Earned on payment ({intent.provider})"))
            # auto-tier from lifetime earned points
            from sqlalchemy import func as _f
            earned = (await db.execute(select(_f.coalesce(_f.sum(LoyaltyLedger.points), 0.0)).where(
                LoyaltyLedger.org_id == intent.org_id, LoyaltyLedger.client_id == client_id,
                LoyaltyLedger.points > 0))).scalar_one()
            cl = (await db.execute(select(Client).where(Client.id == client_id))).scalar_one_or_none()
            if cl:
                lifetime = float(earned) + pts
                cl.loyalty_tier = "gold" if lifetime >= 2000 else ("silver" if lifetime >= 500 else "standard")
    except Exception:
        pass

    # fire a webhook so external systems hear about the payment
    try:
        from backend.services.webhooks import emit_event
        await emit_event(db, intent.org_id, "payment.recorded", {
            "intent_id": intent.id, "order_id": intent.order_id, "amount": intent.amount,
            "provider": intent.provider, "payment_id": pay.id})
    except Exception:
        pass

    # notify the assigned technician that payment landed
    try:
        if intent.order_id:
            o = (await db.execute(select(Order).where(Order.id == intent.order_id))).scalar_one_or_none()
            if o and o.assigned_to_id:
                from backend.services.push import notify
                await notify(db, org_id=intent.org_id, employee_id=o.assigned_to_id,
                             kind="payment.received", title=f"Payment received · {o.order_number}",
                             body=f"{intent.currency.upper()} {intent.amount:.2f} paid",
                             deep_link=f"/tech#/order/{o.id}", commit=False)
    except Exception:
        pass

    await db.commit()
    await db.refresh(intent)
    return intent


# ── Provider status refresh (poll; used where no signed webhook exists) ─────
async def refresh_intent(db: AsyncSession, intent: PaymentIntent) -> PaymentIntent:
    """Poll the provider for the intent's status; reconcile on paid. The public pay page calls
    this, so a provider that's down leaves the status as it was instead of a 500."""
    import httpx
    try:
        return await _refresh_intent(db, intent)
    except httpx.HTTPError as e:
        log.warning("status poll for intent %s failed: %s", intent.id, type(e).__name__)
        return intent


async def _refresh_intent(db: AsyncSession, intent: PaymentIntent) -> PaymentIntent:
    if intent.status == "succeeded":
        return intent
    from backend.services import org_creds
    if intent.provider == "sumup" and intent.provider_ref:
        c = await org_creds.effective(db, intent.org_id, "sumup")
        if c.get("api_key"):
            import httpx
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(f"https://api.sumup.com/v0.1/checkouts/{intent.provider_ref}",
                                     headers={"Authorization": f"Bearer {c['api_key']}"})
            if r.status_code == 200 and (r.json().get("status") or "").upper() == "PAID":
                return await mark_succeeded(db, intent)
    if intent.provider == "square" and intent.provider_ref:
        c = await org_creds.effective(db, intent.org_id, "square")
        if c.get("access_token"):
            import httpx
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(f"{SquareGateway.base_url(c.get('env'))}/v2/orders/{intent.provider_ref}",
                                     headers={"Authorization": f"Bearer {c['access_token']}",
                                              "Square-Version": "2024-06-04"})
            if r.status_code == 200 and (r.json().get("order") or {}).get("state") == "COMPLETED":
                return await mark_succeeded(db, intent)
    return intent


# ── Stripe Terminal (in-person readers; SDK fetches connection tokens) ──────
async def _stripe_key(db: AsyncSession, org_id: str) -> str:
    from backend.services import org_creds
    key = (await org_creds.effective(db, org_id, "stripe")).get("secret_key")
    if not key:
        raise RuntimeError("Stripe is not configured for this shop")
    return key


async def terminal_connection_token(db: AsyncSession, org_id: str) -> str:
    key = await _stripe_key(db, org_id)
    import httpx
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post("https://api.stripe.com/v1/terminal/connection_tokens",
                              auth=(key, ""))
    if r.status_code not in (200, 201):
        raise RuntimeError(f"Stripe {r.status_code}: {r.text[:200]}")
    return r.json()["secret"]


async def terminal_payment_intent(db: AsyncSession, org_id: str, *, amount: float, currency: str, intent_id: str) -> dict:
    key = await _stripe_key(db, org_id)
    import httpx
    data = {"amount": str(minor_units(amount, currency)), "currency": currency,
            "payment_method_types[]": "card_present", "capture_method": "automatic",
            "metadata[intent_id]": intent_id}
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post("https://api.stripe.com/v1/payment_intents", data=data,
                              auth=(key, ""))
    if r.status_code not in (200, 201):
        raise RuntimeError(f"Stripe {r.status_code}: {r.text[:200]}")
    b = r.json()
    return {"provider_ref": b.get("id"), "client_secret": b.get("client_secret")}
