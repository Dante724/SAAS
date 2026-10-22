"""Tekly subscription billing: what a shop's plan allows, and keeping it in step with Stripe.

Shops pay Tekly through Tekly's own Stripe account (STRIPE_BILLING_*), never through the shop's
own payment credentials (services/payments.py, org_creds), which take money from the shop's
customers.

One global price list in USD (Stripe Adaptive Pricing shows it in the shop's currency, Stripe Tax
adds tax): Solo $29 (one location), Shop $69 for the first location + $49 each extra, Pro $129
for the first location + $79 each extra (Shop and Pro are graduated tiered prices; quantity =
active locations). Yearly = 10 x monthly. Shops on custom pricing (India for now, large deals) have
billing_mode "custom": the platform admin sets their plan and term, they never see Checkout, and
plan limits and features apply exactly the same.

Lifecycle (organisations.sub_status):
  trial      14 days from signup, no card, every Pro feature open. Read-only once it ends.
  active     paying (Stripe "active"/"trialing"), or a custom shop inside its term.
  past_due   a renewal failed and Stripe is retrying. Full access for PAST_DUE_GRACE_DAYS.
  unpaid     Stripe gave up retrying.                         -> read-only
  churned    subscription cancelled.                          -> read-only
  trial_lost trial ended without paying (set by the admin).   -> read-only
  suspended  blocked by the platform admin (services/auth.assert_org_active, no access at all).
A read-only shop keeps every record and can read and export all of it; it can't change anything
until it subscribes. Nothing is ever deleted for not paying.

Plans unlock FEATURES by tier and have PLAN_LIMITS (active team members, locations, new jobs a
month). The server enforces them in one place (enforce_request, run for every staff request from
services/auth.get_current_employee; enforce_api_key for public-API keys; assert_can_add /
assert_can_add_job where things are created); the React app and the technician app hide what the
plan lacks using the same data from /api/auth/me (summary()).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional

import httpx
from fastapi import HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models import BillingEvent, Employee, Location, Order, Organisation

log = logging.getLogger("tekly.billing")

PLANS = ("solo", "shop", "pro")
PLAN_NAMES = {"solo": "Solo", "shop": "Shop", "pro": "Pro"}
_RANK = {p: i for i, p in enumerate(PLANS)}
LEGACY_PLANS = {"starter": "shop"}      # the old Starter plan became Shop
PERIODS = ("month", "year")
BILLING_MODES = ("stripe", "custom")
# Display prices in USD; what's charged is the Stripe prices in config. Shop and Pro are per
# location: the first at `month`, each extra at `extra_location`. Yearly = 10 x monthly.
MONTHLY_PRICE = {"solo": {"month": 29}, "shop": {"month": 69, "extra_location": 49, "extra_seat": 4},
                 "pro": {"month": 129, "extra_location": 79}}
PER_LOCATION_PLANS = {p for p, v in MONTHLY_PRICE.items() if "extra_location" in v}
# Plans whose team_members limit is the number *included*: more can be added, each billed as an
# extra seat (a second subscription item, quantity = active members beyond the included pool).
SEAT_PLANS = {p for p, v in MONTHLY_PRICE.items() if "extra_seat" in v}
YEARLY_MONTHS = 10
PAST_DUE_GRACE_DAYS = 14

# Feature -> (label, cheapest plan that includes it). Everything not listed (work orders,
# estimates, invoices, POS, customer portal and booking, the Tekly Go phone app, one-tap
# messaging, network-access switches) is on every plan.
FEATURES: dict[str, tuple[str, str]] = {
    "inventory": ("Inventory, stock and suppliers", "shop"),
    "labels": ("Barcodes, labels and scanning", "shop"),
    "time_clock": ("Time clock and timesheets", "shop"),
    "reports": ("Reports and analytics", "shop"),
    "notification_rules": ("Automatic customer notifications", "shop"),
    "purchase_orders": ("Purchase orders", "pro"),
    "payroll": ("Payroll and commissions", "pro"),
    "multi_location": ("Stock transfers between locations", "pro"),
    "ai_estimates": ("AI estimate drafting and parts recognition", "pro"),
    "accounting_sync": ("QuickBooks and Xero sync", "pro"),
    "sso": ("Single sign-on", "pro"),
    "api_keys": ("API keys", "pro"),
}
# None = unlimited. Pro has no limits. Shop's 8 team members are one pool shared by all its
# locations, and it can add more at MONTHLY_PRICE["shop"]["extra_seat"] each (SEAT_PLANS).
PLAN_LIMITS: dict[str, Optional[dict]] = {
    "solo": {"team_members": 2, "locations": 1, "jobs_per_month": 150},
    "shop": {"team_members": 8, "locations": None, "jobs_per_month": None},
    "pro": None,
}


def features_for(plan: str) -> set[str]:
    return {k for k, (_, need) in FEATURES.items() if _RANK[need] <= _RANK[plan]}


# Which feature each staff API route belongs to, matched in order against the request path.
# writes_only: GETs stay open (a core screen reads them). _OPEN_READS are reads core screens need
# from a gated area.
_OPEN_READS = [re.compile(p) for p in (
    r"^/api/inventory/parts/[^/]+/stock$",   # work order line picker shows stock on hand
)]
_FEATURE_ROUTES: list[tuple[re.Pattern, str, bool]] = [(re.compile(p), f, w) for p, f, w in (
    # Pro
    (r"^/api/inventory/po(/|$)", "purchase_orders", False),
    (r"^/api/inventory/po-charge-types$", "purchase_orders", False),
    (r"^/api/inventory/parts/[^/]+/purchase-orders$", "purchase_orders", False),
    (r"^/api/suppliers/purchase-orders/", "purchase_orders", False),
    (r"^/api/orders/[^/]+/(purchase-orders|incoming-po|create-po)$", "purchase_orders", False),
    (r"^/api/print/purchase-order/", "purchase_orders", False),
    (r"^/api/payroll(/|$)", "payroll", False),
    (r"^/api/print/payroll-statement/", "payroll", False),
    (r"^/api/catalog/services/[^/]+/commissions(/|$)", "payroll", False),
    (r"^/api/transfers(/|$)", "multi_location", False),
    (r"^/api/print/transfers/", "multi_location", False),
    (r"^/api/ai/estimate/draft$", "ai_estimates", False),
    (r"^/api/ai/recognize-items$", "ai_estimates", False),
    (r"^/api/integrations/(qbo|xero)(/|$)", "accounting_sync", False),
    (r"^/api/sso/config(/|$)", "sso", False),
    (r"^/api/api-keys(/|$)", "api_keys", False),
    # Shop
    (r"^/api/print/(templates|labels)(/|$)", "labels", False),
    (r"^/api/documents/render-(labels|asset-labels|client-labels)$", "labels", False),
    (r"^/api/documents/by-barcode/", "labels", False),
    (r"^/api/catalog/products/[^/]+/barcode/generate$", "labels", False),
    (r"^/api/scan(/|$)", "labels", False),
    (r"^/api/assets/scan-resolve$", "labels", False),
    (r"^/api/inventory/scan(-adjust)?$", "labels", False),
    (r"^/api/inventory(/|$)", "inventory", False),
    (r"^/api/(stock-docs|stocktake|bins|conversions|suppliers)(/|$)", "inventory", False),
    (r"^/api/settings/warehouses(/|$)", "inventory", True),
    (r"^/api/orders/[^/]+/(reserve-stock|reservations)$", "inventory", False),
    (r"^/api/orders/[^/]+/lines/[^/]+/receive-stock$", "inventory", False),
    (r"^/api/catalog/products/[^/]+/(serialize|write-off)$", "inventory", False),
    (r"^/api/print/(postings|write-offs|returns|stocktakes)/", "inventory", False),
    (r"^/api/timeclock(/|$)", "time_clock", False),
    (r"^/api/reports/(?!(company-dashboard|home-feed)$)", "reports", False),
    (r"^/api/(analytics|custom-reports)(/|$)", "reports", False),
    (r"^/api/notifications/(rules|quiet-hours)(/|$)", "notification_rules", True),
    (r"^/api/automations(/|$)", "notification_rules", False),
)]

# Writes a read-only shop may still make: signing in and out, 2-step setup, paying, and running
# a report (a POST that only reads). Everything else that isn't GET/HEAD/OPTIONS is refused.
_READ_ONLY_ALLOWED_WRITES = ("/api/auth/", "/api/mfa/", "/api/billing/", "/api/custom-reports/run")
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


# ── State ────────────────────────────────────────────────────────────────────

@dataclass
class BillingState:
    status: str
    plan: str                    # the chosen plan
    effective_plan: str          # what's unlocked right now (Pro during the trial)
    read_only: bool
    reason: Optional[str]        # why it's read-only, as a sentence for the shop
    trial_days_left: Optional[int]
    features: set[str] = field(default_factory=set)

    @property
    def limits(self) -> Optional[dict]:
        lim = PLAN_LIMITS[self.effective_plan]
        return dict(lim) if lim else None


def _plan(org: Organisation) -> str:
    plan = LEGACY_PLANS.get(org.plan or "", org.plan)
    return plan if plan in PLANS else "solo"


def _mode(org: Organisation) -> str:
    return org.billing_mode if org.billing_mode in BILLING_MODES else "stripe"


def state(org: Organisation, today: Optional[date] = None) -> BillingState:
    today = today or date.today()
    status = org.sub_status or "trial"
    plan = _plan(org)
    days_left = None
    read_only, reason = False, None
    if status == "trial":
        if org.trial_ends_at is not None:
            days_left = max(0, (org.trial_ends_at - today).days)
            if today >= org.trial_ends_at:
                read_only, reason = True, "Your free trial has ended."
    elif status == "active":
        # A custom-priced shop's term is set by the admin; past it, the shop is read-only.
        end = org.billing_period_end
        if _mode(org) == "custom" and end is not None and today > end:
            read_only, reason = True, "Your plan's term has ended."
    elif status == "past_due":
        since = org.past_due_since
        if since is not None and (today - since).days > PAST_DUE_GRACE_DAYS:
            read_only, reason = True, "We couldn't take your last payment."
    elif status == "unpaid":
        read_only, reason = True, "We couldn't take your last payment."
    elif status in ("churned", "trial_lost"):
        read_only, reason = True, ("Your free trial has ended." if status == "trial_lost"
                                   else "Your subscription has been cancelled.")
    elif status != "suspended":
        # Unknown state: fail closed to read-only rather than open.
        read_only, reason = True, "Your subscription needs attention."
    effective = "pro" if (status == "trial" and not read_only) else plan
    return BillingState(status, plan, effective, read_only, reason, days_left, features_for(effective))


def has_feature(org: Organisation, feature: str, today: Optional[date] = None) -> bool:
    return feature in state(org, today).features


async def org_has_feature(db: AsyncSession, org_id: str, feature: str) -> bool:
    org = await db.get(Organisation, org_id)
    return org is not None and has_feature(org, feature)


def feature_for_path(path: str, method: str = "GET") -> Optional[str]:
    if method in _SAFE_METHODS and any(p.search(path) for p in _OPEN_READS):
        return None
    for pattern, feature, writes_only in _FEATURE_ROUTES:
        if pattern.search(path):
            if writes_only and method in _SAFE_METHODS:
                return None
            return feature
    return None


def _feature_error(feature: str) -> HTTPException:
    label, need = FEATURES[feature]
    return HTTPException(403, f"{label} — part of the {PLAN_NAMES[need]} plan. "
                              "Upgrade in Settings → Billing to use it.",
                         headers={"X-Plan-Required": need})


def _read_only_error(st: BillingState) -> HTTPException:
    return HTTPException(402, f"{st.reason} Your shop is read-only: you can view and export "
                              "everything, but changes need an active plan (Settings → Billing).",
                         headers={"X-Billing-Read-Only": "1"})


def assert_feature(org: Organisation, feature: str, today: Optional[date] = None) -> None:
    if not has_feature(org, feature, today):
        raise _feature_error(feature)


def enforce_request(org: Optional[Organisation], request: Request,
                    today: Optional[date] = None) -> None:
    """The single server-side gate for staff requests: read-only shops can't write, and each
    route needs its feature's plan. Called from get_current_employee, so every authenticated
    route is covered."""
    if org is None:
        return
    st = state(org, today)
    path, method = request.url.path, request.method
    if st.read_only and method not in _SAFE_METHODS and not path.startswith(_READ_ONLY_ALLOWED_WRITES):
        raise _read_only_error(st)
    feature = feature_for_path(path, method)
    if feature and feature not in st.features:
        raise _feature_error(feature)


def enforce_api_key(org: Optional[Organisation], method: str, today: Optional[date] = None) -> None:
    """Public API keys are a Pro feature, and a read-only shop's keys can only read."""
    if org is None:
        return
    st = state(org, today)
    if "api_keys" not in st.features:
        raise _feature_error("api_keys")
    if st.read_only and method not in _SAFE_METHODS:
        raise _read_only_error(st)


# ── Limits (team members, locations, new jobs a month) ──────────────────────

_LIMIT_NOUN = {"team_members": "active team members", "locations": "location",
               "jobs_per_month": "new jobs a month"}


def _month_start(today: Optional[date] = None) -> datetime:
    t = today or datetime.utcnow().date()
    return datetime(t.year, t.month, 1)


async def jobs_this_month(db: AsyncSession, org_id: str) -> int:
    """New jobs (not estimates) created this calendar month (UTC)."""
    return int((await db.execute(select(func.count()).select_from(Order).where(
        Order.org_id == org_id, Order.is_estimate == False,  # noqa: E712
        Order.created_at >= _month_start()))).scalar() or 0)


async def usage(db: AsyncSession, org_id: str) -> dict:
    members = (await db.execute(select(func.count()).select_from(Employee).where(
        Employee.org_id == org_id, Employee.active == True))).scalar() or 0  # noqa: E712
    locations = (await db.execute(select(func.count()).select_from(Location).where(
        Location.org_id == org_id, Location.active == True))).scalar() or 0  # noqa: E712
    return {"team_members": int(members), "locations": int(locations),
            "jobs_per_month": await jobs_this_month(db, org_id)}


async def _org(db: AsyncSession, org_id: str) -> Organisation:
    return (await db.execute(select(Organisation).where(Organisation.id == org_id))).scalar_one()


def _plan_fitting(what: str, amount: int) -> str:
    """The cheapest plan whose limit for `what` allows `amount`."""
    for p in PLANS:
        lim = PLAN_LIMITS[p]
        if lim is None or lim.get(what) is None or amount <= lim[what]:
            return p
    return "pro"


async def assert_can_add(db: AsyncSession, org_id: str, what: str, adding: int = 1) -> None:
    """Refuse adding a team member / location / job beyond the plan's limit."""
    st = state(await _org(db, org_id))
    cap = (st.limits or {}).get(what)
    if cap is None or (what == "team_members" and st.effective_plan in SEAT_PLANS):
        return   # unlimited, or extra team members are billed as seats
    used = (await usage(db, org_id))[what]
    if used + adding > cap:
        need = _plan_fitting(what, used + adding)
        raise HTTPException(403, f"The {PLAN_NAMES[st.effective_plan]} plan includes up to {cap} "
                                 f"{_LIMIT_NOUN[what]}. Upgrade to {PLAN_NAMES[need]} in "
                                 "Settings → Billing to add more.",
                            headers={"X-Plan-Required": need})


async def assert_can_add_job(db: AsyncSession, org_id: str) -> None:
    """Solo's monthly job cap. Existing jobs keep working; only new ones are refused."""
    await assert_can_add(db, org_id, "jobs_per_month")


def _over_limits(plan: str, used: dict) -> Optional[str]:
    lim = PLAN_LIMITS[plan] or {}
    over = [f"{used[k]} {_LIMIT_NOUN[k]} ({PLAN_NAMES[plan]} allows {v})"
            for k, v in lim.items() if v is not None and k != "jobs_per_month" and used[k] > v
            and not (k == "team_members" and plan in SEAT_PLANS)]
    return "; ".join(over) or None


def extra_seats(plan: str, used: dict) -> int:
    """Team members beyond the plan's included pool, billed as seats (Shop)."""
    if plan not in SEAT_PLANS:
        return 0
    return max(0, used["team_members"] - (PLAN_LIMITS[plan] or {}).get("team_members", 0))


def suggest_plan(used: dict, uses_pro: bool = False) -> str:
    """The plan that fits the shop (shown in the trial's last week).

    Pro only when the shop relies on a Pro feature. Otherwise size decides: Solo if it fits,
    else Shop — Shop has no location cap and takes any team size with $4 extra seats, which
    is cheaper than Pro for a shop that doesn't need Pro's features (the owner's decision)."""
    if uses_pro:
        return "pro"
    if used["team_members"] <= 2 and used["locations"] <= 1:
        return "solo"
    return "shop"


async def pro_features_in_use(db: AsyncSession, org_id: str) -> bool:
    """Has the shop used a Pro-only feature during its trial (it has the data to show for it)?"""
    from backend.models import (
        ApiKey,
        PayrollRun,
        PurchaseOrder,
        QboConnection,
        SsoConnection,
        StockTransfer,
        Supplier,
        XeroConnection,
    )
    checks = (
        select(PurchaseOrder.id).join(Supplier, PurchaseOrder.supplier_id == Supplier.id)
        .where(Supplier.org_id == org_id, PurchaseOrder.status != "cancelled"),
        select(PayrollRun.id).where(PayrollRun.org_id == org_id),
        select(StockTransfer.id).where(StockTransfer.org_id == org_id, StockTransfer.status != "canceled"),
        select(ApiKey.id).where(ApiKey.org_id == org_id, ApiKey.active == True),  # noqa: E712
        select(SsoConnection.id).where(SsoConnection.org_id == org_id, SsoConnection.enabled == True),  # noqa: E712
        select(QboConnection.id).where(QboConnection.org_id == org_id),
        select(XeroConnection.id).where(XeroConnection.org_id == org_id),
    )
    for q in checks:
        if (await db.execute(q.limit(1))).first() is not None:
            return True
    return False


# ── Summary (for /api/auth/me and the Billing page) ─────────────────────────

def summary(org: Optional[Organisation], today: Optional[date] = None) -> Optional[dict]:
    if org is None:
        return None
    st = state(org, today)
    return {
        "status": st.status,
        "plan": st.plan,
        "effective_plan": st.effective_plan,
        "period": org.billing_period if org.billing_period in PERIODS else "month",
        "billing_mode": _mode(org),
        "read_only": st.read_only,
        "read_only_reason": st.reason,
        "trial_ends_at": org.trial_ends_at.isoformat() if org.trial_ends_at else None,
        "trial_days_left": st.trial_days_left,
        "features": sorted(st.features),
        "limits": st.limits,
        "price_locked_until": org.price_locked_until.isoformat() if org.price_locked_until else None,
    }


async def summary_with_usage(db: AsyncSession, org: Optional[Organisation]) -> Optional[dict]:
    """summary() plus usage and, during the trial, the plan that fits the shop."""
    s = summary(org)
    if s is None:
        return None
    used = await usage(db, org.id)
    s["usage"] = used
    s["extra_seats"] = extra_seats(s["effective_plan"], used)
    s["extra_seat_price"] = MONTHLY_PRICE[s["effective_plan"]].get("extra_seat")   # USD a month
    s["suggested_plan"] = (suggest_plan(used, await pro_features_in_use(db, org.id))
                           if s["status"] == "trial" else None)
    return s


def configured() -> bool:
    return (bool(settings.STRIPE_BILLING_SECRET_KEY.strip()) and len(settings.billing_prices) == 6
            and len(settings.billing_seat_prices) == 2)


def price_table() -> dict:
    """USD list prices for display: {plan: {"base": {month, year}, "extra_location"?, "extra_seat"?}}.
    Shop's and Pro's base covers the first location; each extra one adds extra_location."""
    out = {}
    for p, prices in MONTHLY_PRICE.items():
        out[p] = {"base": {"month": prices["month"], "year": prices["month"] * YEARLY_MONTHS}}
        for extra in ("extra_location", "extra_seat"):
            if extra in prices:
                out[p][extra] = {"month": prices[extra], "year": prices[extra] * YEARLY_MONTHS}
    return out


# ── Stripe API (Tekly's platform account) ───────────────────────────────────

async def _stripe(method: str, path: str, data: Optional[dict] = None) -> dict:
    """Form-encoded call to Stripe's REST API with the billing key (tests replace this)."""
    key = settings.STRIPE_BILLING_SECRET_KEY.strip()
    if not key:
        raise HTTPException(503, "Billing isn't set up yet. Please contact support.")
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.request(method, f"https://api.stripe.com/v1{path}", data=data or None,
                                     auth=(key, ""))
    except httpx.HTTPError as e:
        log.error("Stripe billing %s %s failed: %s", method, path, e)
        raise HTTPException(502, "Couldn't reach our payment provider. Please try again.")
    if r.status_code >= 400:
        log.error("Stripe billing %s %s -> %s: %s", method, path, r.status_code, r.text[:300])
        raise HTTPException(502, "Our payment provider didn't accept that. Please try again.")
    return r.json()


def _price(plan: str, period: str) -> str:
    if plan not in PLANS or period not in PERIODS:
        raise HTTPException(422, "Choose plan solo, shop or pro, and period month or year")
    price = settings.billing_prices.get((plan, period))
    if not price:
        raise HTTPException(503, "Billing isn't set up yet. Please contact support.")
    return price


def _seat_price(period: str) -> str:
    price = settings.billing_seat_prices.get(period)
    if not price:
        raise HTTPException(503, "Billing isn't set up yet. Please contact support.")
    return price


def _is_seat_item(item: dict) -> bool:
    price = item.get("price") or {}
    return (price.get("id") in settings.billing_seat_prices.values()
            or (price.get("metadata") or {}).get("kind") == "seat"
            or (item.get("metadata") or {}).get("kind") == "seat")


def _plan_for_price(price_id: Optional[str]) -> Optional[tuple[str, str]]:
    for k, v in settings.billing_prices.items():
        if v == price_id:
            return k
    return None


def _return_url(query: str) -> str:
    base = (settings.PUBLIC_BASE_URL or "").rstrip("/")
    if not base:
        raise HTTPException(503, "Billing isn't set up yet (no PUBLIC_BASE_URL). Please contact support.")
    return f"{base}/settings?section=billing&{query}"


async def _check_plan_fits(db: AsyncSession, org: Organisation, plan: str) -> int:
    """The quantity to bill (Shop and Pro: active locations, tiered; Solo: 1). Refuses a plan the
    shop has outgrown."""
    used = await usage(db, org.id)
    over = _over_limits(plan, used)
    if over:
        raise HTTPException(409, f"Your shop has {over}. Deactivate the extras or choose a bigger plan.")
    return max(1, used["locations"]) if plan in PER_LOCATION_PLANS else 1


def _has_live_subscription(org: Organisation) -> bool:
    return bool(org.stripe_subscription_id) and org.sub_status in ("active", "past_due")


def _assert_self_serve(org: Organisation) -> None:
    if _mode(org) == "custom":
        raise HTTPException(409, "Your plan is managed by Tekly. Contact us to make changes.")


async def create_checkout(db: AsyncSession, org: Organisation, plan: str, period: str,
                          email: Optional[str]) -> str:
    _assert_self_serve(org)
    price = _price(plan, period)
    if _has_live_subscription(org):
        raise HTTPException(409, "You already have a subscription. Change plan or manage billing instead.")
    qty = await _check_plan_fits(db, org, plan)
    seats = extra_seats(plan, await usage(db, org.id))
    data = {
        "mode": "subscription",
        "line_items[0][price]": price,
        "line_items[0][quantity]": str(qty),
        "client_reference_id": org.id,
        "success_url": _return_url("checkout=success"),
        "cancel_url": _return_url("checkout=cancel"),
        "allow_promotion_codes": "true",
        # Stripe Tax on the subscription, with the business's tax ID (VAT, ABN, TRN…) collected.
        # The local-currency price comes from Adaptive Pricing, switched on in the Dashboard.
        "automatic_tax[enabled]": "true",
        "tax_id_collection[enabled]": "true",
        "billing_address_collection": "required",
        "metadata[org_id]": org.id, "metadata[plan]": plan, "metadata[period]": period,
        "subscription_data[metadata][org_id]": org.id,
        "subscription_data[metadata][plan]": plan,
        "subscription_data[metadata][period]": period,
    }
    if seats:
        data["line_items[1][price]"] = _seat_price(period)
        data["line_items[1][quantity]"] = str(seats)
    if org.stripe_customer_id:
        data["customer"] = org.stripe_customer_id
        # Required with an existing customer for tax: save the address and name entered here.
        data["customer_update[address]"] = "auto"
        data["customer_update[name]"] = "auto"
    elif email:
        data["customer_email"] = email
    session = await _stripe("POST", "/checkout/sessions", data)
    return session["url"]


async def create_portal(org: Organisation) -> str:
    _assert_self_serve(org)
    if not org.stripe_customer_id:
        raise HTTPException(409, "There's no billing account yet. Choose a plan first.")
    s = await _stripe("POST", "/billing_portal/sessions", {
        "customer": org.stripe_customer_id, "return_url": _return_url("portal=return")})
    return s["url"]


async def change_plan(db: AsyncSession, org: Organisation, plan: str, period: str) -> None:
    """Switch a paying shop's plan in place (prorated). Moving to a new plan or period means the
    current list price for it, so a founding-price lock (price_locked_until) only covers the plan
    and period the shop already has; the shop is told before it switches (Billing page)."""
    _assert_self_serve(org)
    price = _price(plan, period)
    if not _has_live_subscription(org) or not org.stripe_subscription_item_id:
        raise HTTPException(409, "There's no active subscription to change. Choose a plan first.")
    qty = await _check_plan_fits(db, org, plan)
    seats = extra_seats(plan, await usage(db, org.id))
    data = {
        "items[0][id]": org.stripe_subscription_item_id,
        "items[0][price]": price,
        "items[0][quantity]": str(qty),
        "proration_behavior": "create_prorations",
        "metadata[plan]": plan, "metadata[period]": period,
    }
    # Extra team members (Shop): add, re-price or drop the seat item to match the new plan.
    if seats:
        if org.stripe_seat_item_id:
            data["items[1][id]"] = org.stripe_seat_item_id
        data["items[1][price]"] = _seat_price(period)
        data["items[1][quantity]"] = str(seats)
    elif org.stripe_seat_item_id:
        data["items[1][id]"] = org.stripe_seat_item_id
        data["items[1][deleted]"] = "true"
    sub = await _stripe("POST", f"/subscriptions/{org.stripe_subscription_id}", data)
    _apply_subscription(org, sub)


async def sync_quantity(db: AsyncSession, org_id: str) -> None:
    """Shop and Pro bill per active location: after one is added or archived, update the subscription's
    quantity — never its price, so a founding-price lock is kept. Best-effort: a Stripe hiccup
    must not undo the shop's change."""
    if not configured():
        return
    org = await _org(db, org_id)
    if _mode(org) == "custom" or _plan(org) not in PER_LOCATION_PLANS \
            or not _has_live_subscription(org) or not org.stripe_subscription_item_id:
        return
    qty = max(1, (await usage(db, org_id))["locations"])
    try:
        await _stripe("POST", f"/subscriptions/{org.stripe_subscription_id}", {
            "items[0][id]": org.stripe_subscription_item_id,
            "items[0][quantity]": str(qty),
            "proration_behavior": "create_prorations",
        })
    except HTTPException:
        log.warning("Could not update billed locations for org %s to %s", org_id, qty)


async def sync_seats(db: AsyncSession, org_id: str) -> None:
    """Shop bills team members beyond its included 8 as seats: after someone is added, invited
    in, deactivated or removed, match the seat item's quantity. Best-effort, like sync_quantity."""
    if not configured():
        return
    org = await _org(db, org_id)
    plan = _plan(org)
    if _mode(org) == "custom" or plan not in SEAT_PLANS or not _has_live_subscription(org):
        return
    seats = extra_seats(plan, await usage(db, org_id))
    try:
        if org.stripe_seat_item_id and seats == 0:
            await _stripe("DELETE", f"/subscription_items/{org.stripe_seat_item_id}",
                          {"proration_behavior": "create_prorations"})
            org.stripe_seat_item_id = None
        elif org.stripe_seat_item_id:
            await _stripe("POST", f"/subscription_items/{org.stripe_seat_item_id}",
                          {"quantity": str(seats), "proration_behavior": "create_prorations"})
        elif seats:
            item = await _stripe("POST", "/subscription_items", {
                "subscription": org.stripe_subscription_id,
                "price": _seat_price(org.billing_period if org.billing_period in PERIODS else "month"),
                "quantity": str(seats), "proration_behavior": "create_prorations",
                "metadata[kind]": "seat"})
            org.stripe_seat_item_id = item.get("id")
        await db.commit()
    except HTTPException:
        log.warning("Could not update billed team members for org %s to %s extra", org_id, seats)


# ── Webhook events ───────────────────────────────────────────────────────────

HANDLED_EVENTS = ("checkout.session.completed", "customer.subscription.created",
                  "customer.subscription.updated", "customer.subscription.deleted",
                  "invoice.payment_failed")

# Stripe subscription status -> sub_status. "incomplete" means the first payment hasn't gone
# through yet: leave the shop as it is.
_STATUS_MAP = {"active": "active", "trialing": "active", "past_due": "past_due",
               "unpaid": "unpaid", "paused": "unpaid", "canceled": "churned",
               "incomplete_expired": "churned"}


def _ts_date(ts) -> Optional[date]:
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).date()
    except (TypeError, ValueError):
        return None


def _apply_subscription(org: Organisation, sub: dict) -> None:
    org.stripe_subscription_id = sub.get("id") or org.stripe_subscription_id
    if sub.get("customer") and not org.stripe_customer_id:
        org.stripe_customer_id = sub["customer"]
    items = ((sub.get("items") or {}).get("data") or [])
    seat = next((i for i in items if _is_seat_item(i)), None)
    item = next((i for i in items if not _is_seat_item(i)), {})
    if items:
        org.stripe_seat_item_id = seat.get("id") if seat else None
    if item.get("id"):
        org.stripe_subscription_item_id = item["id"]
    meta = sub.get("metadata") or {}
    price_id = (item.get("price") or {}).get("id")
    if price_id:
        org.stripe_price_id = price_id
    plan_period = _plan_for_price(price_id)
    if plan_period:
        org.plan, org.billing_period = plan_period
    elif LEGACY_PLANS.get(meta.get("plan"), meta.get("plan")) in PLANS:
        # A price no longer in config (e.g. a founding customer's locked price): trust metadata.
        org.plan = LEGACY_PLANS.get(meta["plan"], meta["plan"])
        if meta.get("period") in PERIODS:
            org.billing_period = meta["period"]
    new_status = _STATUS_MAP.get(sub.get("status") or "")
    if new_status:
        if new_status == "past_due":
            org.past_due_since = org.past_due_since or date.today()
        else:
            org.past_due_since = None
        org.sub_status = new_status
    org.cancel_at_period_end = bool(sub.get("cancel_at_period_end"))
    end = sub.get("current_period_end") or item.get("current_period_end")
    org.billing_period_end = _ts_date(end) or org.billing_period_end


async def _find_org(db: AsyncSession, obj: dict) -> Optional[Organisation]:
    meta = obj.get("metadata") or {}
    sub_meta = ((obj.get("subscription_details") or {}).get("metadata")
                or (((obj.get("parent") or {}).get("subscription_details") or {}).get("metadata"))
                or {})
    org_id = meta.get("org_id") or sub_meta.get("org_id") or obj.get("client_reference_id")
    if org_id:
        org = (await db.execute(select(Organisation).where(Organisation.id == org_id))).scalar_one_or_none()
        if org:
            return org
    sub_id = obj.get("subscription") if isinstance(obj.get("subscription"), str) else None
    if obj.get("object") == "subscription":
        sub_id = obj.get("id")
    if sub_id:
        org = (await db.execute(select(Organisation).where(
            Organisation.stripe_subscription_id == sub_id))).scalar_one_or_none()
        if org:
            return org
    if isinstance(obj.get("customer"), str):
        return (await db.execute(select(Organisation).where(
            Organisation.stripe_customer_id == obj["customer"]))).scalar_one_or_none()
    return None


async def handle_event(db: AsyncSession, event: dict) -> dict:
    """Apply one verified Stripe Billing event, exactly once. The event id is stored in
    billing_events (primary key) in the same transaction as its effect: a redelivery finds it
    and stops, and if applying fails nothing is stored, so Stripe's retry gets a clean run."""
    event_id, etype = event.get("id"), event.get("type")
    if not event_id or not etype:
        raise HTTPException(400, "Not a Stripe event")
    if etype not in HANDLED_EVENTS:
        return {"ok": True, "ignored": etype}
    if await db.get(BillingEvent, event_id) is not None:
        return {"ok": True, "duplicate": True}
    obj = (event.get("data") or {}).get("object") or {}
    org = await _find_org(db, obj)
    db.add(BillingEvent(id=event_id, type=etype, org_id=org.id if org else None))
    try:
        await db.flush()
    except IntegrityError:          # the same event delivered twice at once
        await db.rollback()
        return {"ok": True, "duplicate": True}
    if org is None:
        log.warning("Stripe billing event %s (%s) matches no shop", event_id, etype)
        await db.commit()
        return {"ok": True, "unmatched": True}

    customer = obj.get("customer") if isinstance(obj.get("customer"), str) else None
    if customer and org.stripe_customer_id and customer != org.stripe_customer_id:
        log.warning("Stripe billing event %s: customer %s is not shop %s's", event_id, customer, org.id)
        await db.commit()
        return {"ok": True, "ignored": "customer mismatch"}

    created = event.get("created")
    created = int(created) if isinstance(created, (int, float)) else None
    # Stripe doesn't promise order: an event older than the last one applied doesn't change status.
    stale = created is not None and org.billing_event_at is not None and created < org.billing_event_at

    if etype == "checkout.session.completed":
        if obj.get("mode") == "subscription":
            if customer and not org.stripe_customer_id:
                org.stripe_customer_id = customer
            if isinstance(obj.get("subscription"), str):
                org.stripe_subscription_id = obj["subscription"]
            meta = obj.get("metadata") or {}
            paid = obj.get("status") == "complete" and \
                obj.get("payment_status") in ("paid", "no_payment_required")
            if paid and not stale:
                org.sub_status, org.past_due_since = "active", None
                plan = LEGACY_PLANS.get(meta.get("plan"), meta.get("plan"))
                if plan in PLANS:
                    org.plan = plan
                if meta.get("period") in PERIODS:
                    org.billing_period = meta["period"]
    elif etype in ("customer.subscription.created", "customer.subscription.updated"):
        if not stale:
            _apply_subscription(org, obj)
    elif etype == "customer.subscription.deleted":
        # Only the shop's current subscription: deleting an old one after a re-subscribe is not a cancel.
        current = org.stripe_subscription_id is None or obj.get("id") in (None, org.stripe_subscription_id)
        if not stale and current:
            org.sub_status, org.cancel_at_period_end, org.past_due_since = "churned", False, None
    elif etype == "invoice.payment_failed":
        if not stale and org.sub_status == "active":
            org.sub_status = "past_due"
            org.past_due_since = org.past_due_since or date.today()

    if created is not None and not stale:
        org.billing_event_at = created
    await db.commit()
    return {"ok": True}
