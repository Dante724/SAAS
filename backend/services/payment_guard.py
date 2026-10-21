"""F4 — live-money guardrail.

Only test/sandbox payment credentials work unless BOTH ENVIRONMENT=production and
ALLOW_LIVE_PAYMENTS=true. A live Stripe key (sk_live_/rk_live_) or a Square account set to the
production environment is refused when it is saved and again whenever it would be used, so a
staging box or a developer laptop can never charge a real card.
"""
from backend.config import settings


class LivePaymentsRefused(ValueError):
    """Raised when live credentials are used where live money is not allowed."""


def live_payments_allowed() -> bool:
    return settings.is_production and bool(settings.ALLOW_LIVE_PAYMENTS)


def is_live_stripe_key(key: str | None) -> bool:
    return (key or "").strip().startswith(("sk_live_", "rk_live_"))


def is_live_square(creds: dict) -> bool:
    return (creds.get("env") or settings.SQUARE_ENV or "sandbox").strip().lower() == "production"


def creds_are_live(provider: str, creds: dict) -> bool:
    if provider == "stripe":
        return is_live_stripe_key(creds.get("secret_key"))
    if provider == "square":
        return is_live_square(creds)
    return False


def refusal_message(provider: str) -> str:
    what = {"stripe": "Live Stripe keys", "square": "Square's production environment"}.get(provider, "Live keys")
    return (f"{what} can't be used here. This server only takes test payments. "
            "Live payments need ENVIRONMENT=production and ALLOW_LIVE_PAYMENTS=true.")


def assert_allowed(provider: str, creds: dict) -> None:
    """Refuse live credentials unless live payments are switched on in production."""
    if creds_are_live(provider, creds) and not live_payments_allowed():
        raise LivePaymentsRefused(refusal_message(provider))


def test_mode(provider: str, creds: dict) -> bool:
    """True when this provider would only move test money."""
    return not (creds_are_live(provider, creds) and live_payments_allowed())
