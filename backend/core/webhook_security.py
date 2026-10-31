"""Build 110 — production hardening: webhook authentication and rate limiting.

Two small, self-contained defenses:

1.  Org webhook tokens. Inbound provider webhooks (unified inbox, VoIP calls) are keyed
    by org id in the URL — which is guessable/leakable. Every org gets a random
    ``webhook_secret`` when it is created; inbound posts must present it in an
    ``X-Webhook-Token`` header. Fail-closed (whh1, B8): the migration backfilled orgs that
    had none, and an org without a token, an unknown org and a wrong token all get the same
    401, so the endpoint can't be used to learn which org ids exist. 120 posts/min per org.

2.  A sliding-window rate limiter for public endpoints. ``/api/public/lookup`` pairs an
    order number with a phone; without throttling it can be brute-forced. The limiter is
    in-process (a deque of timestamps per key) — the right tool for a single-node deploy
    and an honest placeholder for Redis when there are multiple nodes.
"""
import hmac
import logging
import time
from collections import defaultdict, deque
from typing import Optional

from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models import Organisation

log = logging.getLogger("tekly")


INBOUND_TOKEN_LIMIT_PER_MIN = 120
_BAD_TOKEN = "Invalid or missing X-Webhook-Token"


async def verify_org_webhook(db: AsyncSession, org_id: str, request: Request) -> None:
    """Reject an inbound webhook that doesn't present the org's token. Fails closed."""
    from backend.services.ratelimit import aenforce
    await aenforce("inbound_token_webhook", (org_id or "")[:64], INBOUND_TOKEN_LIMIT_PER_MIN, 60)
    org = (await db.execute(select(Organisation).where(
        Organisation.id == org_id))).scalar_one_or_none()
    expected = (org.webhook_secret or "") if org else ""
    presented = request.headers.get("X-Webhook-Token", "")
    # Compare even when there is nothing to compare against, so timing doesn't tell them apart.
    ok = hmac.compare_digest(presented.encode(), (expected or "\0" * 32).encode())
    if not (org and expected and presented and ok):
        raise HTTPException(401, _BAD_TOKEN)


# ── Provider-signed webhooks (Stripe, Square, Meta, Twilio) ──────────────────

def unsigned_webhooks_allowed() -> bool:
    """Only local development and the test suite may accept unsigned provider events.
    Staging and production always fail closed, and so does a server where ENVIRONMENT was
    never set (that now means production; it used to default to "development", so a deploy that
    forgot it accepted forged webhooks)."""
    return ("ENVIRONMENT" in settings.model_fields_set
            and (settings.ENVIRONMENT or "").lower() in ("development", "test"))


async def provider_secrets(db: AsyncSession, org_id: str, provider: str, field: str,
                           global_secret: Optional[str]) -> list[str]:
    """Signing secrets that may have signed an event for this org: the shop's own
    (Settings → Integrations) first, then the operator-wide env value."""
    from backend.services import org_creds
    try:
        own = (await org_creds.effective(db, org_id, provider, read_only=True)).get(field)   # verify only
    except org_creds.CredentialsUnreadable:
        own = None   # the shop's secret can't be read: only the operator's may verify (or none)
    return [s for s in dict.fromkeys([own, global_secret]) if s]


def require_signature(secrets: list[str], verified: bool, provider: str, status: int = 400) -> None:
    """Enforce a provider signature: when any secret exists the event must verify against
    one; with none configured, reject everywhere except development/test."""
    if secrets:
        if not verified:
            raise HTTPException(status, "Invalid signature")
    elif not unsigned_webhooks_allowed():
        log.warning("%s webhook rejected: no signing secret configured for this shop", provider)
        raise HTTPException(status, "Webhook signing secret not configured")


class SlidingWindowLimiter:
    """Tiny in-process sliding-window limiter: at most ``limit`` events per ``window``
    seconds per key. Keys are caller-chosen (client IP, org id, order number...)."""

    def __init__(self, limit: int, window_s: float):
        self.limit = limit
        self.window = window_s
        self._hits: dict[str, deque] = defaultdict(deque)

    def check(self, key: str) -> bool:
        """True if the event is allowed; records it if so."""
        now = time.monotonic()
        dq = self._hits[key]
        while dq and now - dq[0] > self.window:
            dq.popleft()
        if len(dq) >= self.limit:
            return False
        dq.append(now)
        return True

    def reset(self) -> None:  # for tests
        self._hits.clear()


# Public lookup: generous for a human retyping a phone, hostile to a script.
lookup_limiter = SlidingWindowLimiter(limit=10, window_s=60)


def enforce_rate_limit(limiter: SlidingWindowLimiter, key: str) -> None:
    if not limiter.check(key):
        raise HTTPException(429, "Too many attempts — try again in a minute")


def client_ip(request: Request) -> str:
    from backend.services.ratelimit import client_ip as _client_ip
    return _client_ip(request)
