"""What every shop and every location needs on day one (launch blockers B2, B4, B6).

* A location runs on the shop's clock: a new location copies the company timezone instead of
  starting on UTC, so reminders, bookings and "today" are measured on the right clock.
* A location can take money: it gets a Cash drawer and a Card account, so the first payment
  doesn't stop at "Select a cashbox/account".
* Sign-up picks a country, and the country decides currency, timezone and the default tax, which
  is attached to the sales tax stack so it is actually charged.
"""
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Account, Location, Organisation, Tax, TaxRule

# The sales documents a default tax applies to (work orders, invoices and POS sales all read
# the "tickets_sales" stack; services/tax.py).
SALES_DOC_FAMILY = "tickets_sales"

# Launch countries for the sign-up Country picker: currency, timezone and the default tax.
# (name, rate %, mode) — "included" = VAT-style, already in the price; "imposed" = added on top.
# A US shop starts at 0% because sales tax depends on the state; the setup guide asks for it.
COUNTRIES: dict[str, dict] = {
    "US": {"label": "United States", "region": "us", "currency_code": "USD", "currency": "$",
           "timezone": "America/New_York", "tax": ("Sales tax", 0.0, "imposed")},
    "CA": {"label": "Canada", "region": "ca", "currency_code": "CAD", "currency": "$",
           "timezone": "America/Toronto", "tax": ("GST", 5.0, "imposed")},
    "GB": {"label": "United Kingdom", "region": "eu", "currency_code": "GBP", "currency": "£",
           "timezone": "Europe/London", "tax": ("VAT", 20.0, "included")},
    "IE": {"label": "Ireland", "region": "eu", "currency_code": "EUR", "currency": "€",
           "timezone": "Europe/Dublin", "tax": ("VAT", 23.0, "included")},
    "DE": {"label": "Germany", "region": "eu", "currency_code": "EUR", "currency": "€",
           "timezone": "Europe/Berlin", "tax": ("MwSt", 19.0, "included")},
    "FR": {"label": "France", "region": "eu", "currency_code": "EUR", "currency": "€",
           "timezone": "Europe/Paris", "tax": ("TVA", 20.0, "included")},
    "ES": {"label": "Spain", "region": "eu", "currency_code": "EUR", "currency": "€",
           "timezone": "Europe/Madrid", "tax": ("IVA", 21.0, "included")},
    "IT": {"label": "Italy", "region": "eu", "currency_code": "EUR", "currency": "€",
           "timezone": "Europe/Rome", "tax": ("IVA", 22.0, "included")},
    "NL": {"label": "Netherlands", "region": "eu", "currency_code": "EUR", "currency": "€",
           "timezone": "Europe/Amsterdam", "tax": ("BTW", 21.0, "included")},
    "BE": {"label": "Belgium", "region": "eu", "currency_code": "EUR", "currency": "€",
           "timezone": "Europe/Brussels", "tax": ("BTW", 21.0, "included")},
    "PT": {"label": "Portugal", "region": "eu", "currency_code": "EUR", "currency": "€",
           "timezone": "Europe/Lisbon", "tax": ("IVA", 23.0, "included")},
    "AT": {"label": "Austria", "region": "eu", "currency_code": "EUR", "currency": "€",
           "timezone": "Europe/Vienna", "tax": ("USt", 20.0, "included")},
    "PL": {"label": "Poland", "region": "eu", "currency_code": "PLN", "currency": "zł",
           "timezone": "Europe/Warsaw", "tax": ("VAT", 23.0, "included")},
    "SE": {"label": "Sweden", "region": "eu", "currency_code": "SEK", "currency": "kr",
           "timezone": "Europe/Stockholm", "tax": ("Moms", 25.0, "included")},
    "IN": {"label": "India", "region": "other", "currency_code": "INR", "currency": "₹",
           "timezone": "Asia/Kolkata", "tax": ("GST", 18.0, "imposed")},
}


def country_defaults(country: Optional[str]) -> Optional[dict]:
    return COUNTRIES.get((country or "").strip().upper())


def country_options() -> list[dict]:
    """The sign-up picker: launch countries, most common first, then the rest by name."""
    first = ["US", "GB", "CA", "IN"]
    rest = sorted((c for c in COUNTRIES if c not in first), key=lambda c: COUNTRIES[c]["label"])
    return [{"code": c, "label": COUNTRIES[c]["label"], "currency_code": COUNTRIES[c]["currency_code"],
             "currency": COUNTRIES[c]["currency"], "timezone": COUNTRIES[c]["timezone"],
             "tax_name": COUNTRIES[c]["tax"][0], "tax_rate": COUNTRIES[c]["tax"][1],
             "tax_mode": COUNTRIES[c]["tax"][2]} for c in first + rest]


async def seed_default_tax(db: AsyncSession, org_id: str, name: str, rate: float, mode: str) -> Tax:
    """Create the shop's default tax and put it on the sales stack, so it is charged. (A tax
    with no stack entry was never applied: 0% on every job while the setup guide said done.)"""
    tax = Tax(org_id=org_id, name=name, rate=rate, mode=mode, is_default=True, active=True)
    db.add(tax)
    await db.flush()
    db.add(TaxRule(org_id=org_id, doc_family=SALES_DOC_FAMILY, tax_id=tax.id, priority=0))
    await db.flush()
    return tax


async def ensure_default_on_stack(db: AsyncSession, org_id: str, tax: Tax) -> None:
    """A tax marked default joins an empty sales stack. An owner who already ordered the stack
    by hand keeps their order."""
    if not tax.is_default or not tax.active:
        return
    has_stack = (await db.execute(select(TaxRule.id).where(
        TaxRule.org_id == org_id, TaxRule.doc_family == SALES_DOC_FAMILY).limit(1))).scalar_one_or_none()
    if not has_stack:
        db.add(TaxRule(org_id=org_id, doc_family=SALES_DOC_FAMILY, tax_id=tax.id, priority=0))
        await db.flush()


async def ensure_cash_accounts(db: AsyncSession, location: Location, *, first: bool = False) -> list[Account]:
    """A Cash drawer and a Card account for this location, unless it already has them."""
    have = set((await db.execute(select(Account.payment_option).where(
        Account.org_id == location.org_id, Account.location_id == location.id,
        Account.active == True))).scalars().all())  # noqa: E712
    suffix = "" if first else f" ({location.name})"
    made = []
    for option, name, icon in (("cash", "Cash drawer", "payments"), ("card", "Card", "credit_card")):
        if option not in have:
            acc = Account(org_id=location.org_id, location_id=location.id, name=f"{name}{suffix}"[:120],
                          icon=icon, payment_option=option)
            db.add(acc)
            made.append(acc)
    if made:
        await db.flush()
    return made


async def setup_location(db: AsyncSession, location: Location, org: Optional[Organisation] = None,
                         *, first: bool = False) -> Location:
    """Give a just-created location the shop's timezone and its cash accounts. Call after the
    location is added (it flushes)."""
    if not location.timezone or location.timezone == "UTC":
        if org is None:
            org = (await db.execute(select(Organisation).where(
                Organisation.id == location.org_id))).scalar_one_or_none()
        if org is not None and org.timezone:
            location.timezone = org.timezone
    await db.flush()
    await ensure_cash_accounts(db, location, first=first)
    return location
