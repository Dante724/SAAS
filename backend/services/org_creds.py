"""144jn — per-org (per-shop) integration credentials for multi-tenant SaaS.

Each shop stores its OWN provider keys (Stripe/Square/SumUp, Twilio, SMTP, Meta)
in `org_integration_credentials`, encrypted at rest with Fernet. The effective()
resolver returns the shop's own keys when set, and only falls back to the global
server env as an operator-wide default — so two shops never share one payment or
messaging account.

Design notes
------------
* Encryption key is derived from SECRET_KEY (already required at boot), so no new
  secret to manage. Rotating SECRET_KEY invalidates stored creds (they'd need
  re-entry) — acceptable and documented.
* Secret fields are never returned to the client in cleartext; status() masks
  them. The raw values are only read server-side at send/charge time.
"""
import base64
import hashlib
import json
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models import OrgIntegrationCredential
from backend.services.payment_guard import LivePaymentsRefused

# ── Provider field schemas ───────────────────────────────────────────────────
# `secret` fields are masked on read. `env` maps a field to the global-env
# fallback so an operator default keeps working until a shop enters its own.
PROVIDER_FIELDS: dict[str, list[dict]] = {
    "stripe": [
        {"key": "secret_key", "label": "Secret key (sk_…)", "secret": True, "env": "STRIPE_SECRET_KEY"},
        {"key": "webhook_secret", "label": "Webhook signing secret (whsec_…)", "secret": True, "env": "STRIPE_WEBHOOK_SECRET"},
        {"key": "success_url", "label": "Success URL", "env": "STRIPE_SUCCESS_URL"},
        {"key": "cancel_url", "label": "Cancel URL", "env": "STRIPE_CANCEL_URL"},
    ],
    "square": [
        {"key": "access_token", "label": "Access token", "secret": True, "env": "SQUARE_ACCESS_TOKEN"},
        {"key": "location_id", "label": "Location ID", "env": "SQUARE_LOCATION_ID"},
        {"key": "env", "label": "Environment (sandbox/production)", "env": "SQUARE_ENV"},
        {"key": "webhook_signature_key", "label": "Webhook signature key", "secret": True, "env": "SQUARE_WEBHOOK_SIGNATURE_KEY"},
    ],
    "sumup": [
        {"key": "api_key", "label": "API key", "secret": True, "env": "SUMUP_API_KEY"},
        {"key": "merchant_code", "label": "Merchant code", "env": "SUMUP_MERCHANT_CODE"},
    ],
    "twilio": [
        {"key": "account_sid", "label": "Account SID", "secret": True, "env": "TWILIO_ACCOUNT_SID"},
        {"key": "auth_token", "label": "Auth token", "secret": True, "env": "TWILIO_AUTH_TOKEN"},
        {"key": "from", "label": "SMS from number", "env": "TWILIO_FROM"},
        {"key": "whatsapp_from", "label": "WhatsApp from number", "env": "TWILIO_WHATSAPP_FROM"},
    ],
    "smtp": [
        {"key": "host", "label": "SMTP host", "env": "SMTP_HOST"},
        {"key": "port", "label": "SMTP port", "env": "SMTP_PORT"},
        {"key": "user", "label": "Username", "env": "SMTP_USER"},
        {"key": "password", "label": "Password", "secret": True, "env": "SMTP_PASSWORD"},
        {"key": "from", "label": "From address", "env": "SMTP_FROM"},
        {"key": "use_tls", "label": "Use TLS (true/false)", "env": "SMTP_USE_TLS"},
    ],
    "meta": [
        {"key": "app_secret", "label": "App secret", "secret": True, "env": "META_APP_SECRET"},
        {"key": "verify_token", "label": "Verify token", "secret": True, "env": "META_VERIFY_TOKEN"},
        {"key": "page_token", "label": "Page access token", "secret": True, "env": "META_PAGE_TOKEN"},
        {"key": "ig_token", "label": "Instagram token", "secret": True, "env": "META_IG_TOKEN"},
        {"key": "whatsapp_phone_id", "label": "WhatsApp phone ID", "env": "WHATSAPP_PHONE_ID"},
        {"key": "whatsapp_token", "label": "WhatsApp token", "secret": True, "env": "WHATSAPP_TOKEN"},
        # S2: the shop's own Meta ids. Inbound webhooks only accept events for these, and the
        # operator-wide app secret is only trusted for shops that registered them.
        {"key": "page_id", "label": "Facebook Page ID"},
        {"key": "ig_account_id", "label": "Instagram account ID"},
    ],
}

PROVIDERS = tuple(PROVIDER_FIELDS.keys())

# Credentials managed by their own feature screen rather than the generic Integrations
# editor: not listed by status(), not writable through /api/integrations/credentials, and
# never backed by a global env default (each shop's own account or nothing).
# D1 — Twilio voice: written only by /api/telephony/twilio, which probes the keys first and
# records whether they are test or live (`mode`), so the live-key guard can't be skipped.
INTERNAL_PROVIDER_FIELDS: dict[str, list[dict]] = {
    "twilio_voice": [
        {"key": "account_sid", "label": "Account SID", "secret": True},
        {"key": "auth_token", "label": "Auth token", "secret": True},
        {"key": "mode", "label": "Mode (test/live, set by the server)"},
    ],
    # WS-C — Aircall: the token Aircall puts in every webhook payload. Written only by
    # /api/telephony/aircall, and only while the server switch AIRCALL_ENABLED is on.
    "aircall_voice": [
        {"key": "webhook_token", "label": "Webhook token", "secret": True},
    ],
}


def _fields(provider: str) -> list[dict]:
    return PROVIDER_FIELDS.get(provider) or INTERNAL_PROVIDER_FIELDS.get(provider, [])


# ── Encryption ───────────────────────────────────────────────────────────────


class CredentialsUnreadable(LivePaymentsRefused):
    """A shop's saved keys exist but can't be decrypted (key rotated away, damaged row).

    Audit M10: this used to read as "no keys", so the operator's own env keys were used for that
    shop. It fails closed instead. It is a LivePaymentsRefused so every payment path that already
    refuses live keys refuses these the same way, with this message."""


def _unreadable(provider: str) -> CredentialsUnreadable:
    return CredentialsUnreadable(f"This shop's saved {provider} keys can't be read. "
                                 "Enter them again in Settings › Integrations.")


def _legacy_fernet() -> Fernet:
    """The key derived from SECRET_KEY (the sign-in key) that credentials used before
    TOKEN_ENCRYPTION_KEY existed. Only for local dev without a key, and for migration aud10m01."""
    digest = hashlib.sha256((settings.SECRET_KEY or "").encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _fernet() -> MultiFernet:
    """TOKEN_ENCRYPTION_KEY (required when deployed). Without one — local dev and tests — the
    legacy SECRET_KEY-derived key is used; it is never mixed into a real key ring, so rows saved
    under it must be moved over by migration aud10m01."""
    from backend.services.crypto import fernet_keys
    return MultiFernet(fernet_keys() or [_legacy_fernet()])


def _encrypt(data: dict) -> str:
    return _fernet().encrypt(json.dumps(data).encode("utf-8")).decode("ascii")


def _decrypt(token: Optional[str], provider: str = "integration") -> dict:
    if not token:
        return {}
    try:
        data = json.loads(_fernet().decrypt(token.encode("ascii")).decode("utf-8"))
    except (InvalidToken, ValueError, TypeError, UnicodeError):
        raise _unreadable(provider) from None
    if not isinstance(data, dict):
        raise _unreadable(provider)
    return data


def reencrypt_legacy(token: Optional[str]) -> Optional[str]:
    """Ciphertext under the legacy key → the same data under TOKEN_ENCRYPTION_KEY. None when
    there's nothing to do: no dedicated key, already readable with it, or unreadable by both."""
    from backend.services.crypto import fernet_keys
    keys = fernet_keys()
    if not token or not keys:
        return None
    ring = MultiFernet(keys)
    try:
        ring.decrypt(token.encode("ascii"))
        return None
    except (InvalidToken, ValueError, TypeError, UnicodeError):
        pass
    try:
        plain = _legacy_fernet().decrypt(token.encode("ascii"))
    except (InvalidToken, ValueError, TypeError, UnicodeError):
        return None
    return ring.encrypt(plain).decode("ascii")


# ── Env fallback ─────────────────────────────────────────────────────────────
def with_env_defaults(provider: str, creds: dict) -> dict:
    """The shop's keys with any blank field filled from the operator env, then put through the
    live-money guard (F4). For code that used to write `creds.get(k) or settings.X`, which skipped
    the guard when the shop's row lacked that field (review S6)."""
    merged = {**_env_creds(provider), **{k: v for k, v in (creds or {}).items() if v not in (None, "")}}
    from backend.services.payment_guard import assert_allowed
    assert_allowed(provider, merged)
    return merged


def _env_creds(provider: str) -> dict:
    out = {}
    for f in _fields(provider):
        env = f.get("env")
        val = getattr(settings, env, None) if env else None
        if val not in (None, ""):
            out[f["key"]] = val
    return out


# ── Reads ────────────────────────────────────────────────────────────────────
async def _row(db: AsyncSession, org_id: str, provider: str) -> Optional[OrgIntegrationCredential]:
    return (await db.execute(select(OrgIntegrationCredential).where(
        OrgIntegrationCredential.org_id == org_id,
        OrgIntegrationCredential.provider == provider))).scalar_one_or_none()


async def own(db: AsyncSession, org_id: str, provider: str) -> dict:
    """Only this shop's saved keys, with no operator env fallback (empty when none)."""
    row = await _row(db, org_id, provider)
    return _decrypt(row.data_enc, provider) if (row and row.active) else {}


async def effective(db: AsyncSession, org_id: str, provider: str, *, read_only: bool = False,
                    own_only: bool = False) -> dict:
    """The credentials to actually use for this shop: its own keys if configured
    and active, otherwise the operator-wide env defaults (may be empty).

    F4: for payment providers this is also the live-money gate. Live Stripe keys or Square's
    production env raise payment_guard.LivePaymentsRefused unless live payments are switched on,
    so every caller that would move money is covered. Callers that only inspect the keys (status
    badges, webhook signature lookup) pass read_only=True.

    own_only=True skips the operator-wide env defaults: card readers (F2/F3) must be the shop's own
    Stripe/Square account, never one shared by every shop on the server (review P8)."""
    row = await _row(db, org_id, provider)
    data = None
    if row and row.active:
        data = _decrypt(row.data_enc, provider)   # unreadable raises: never fall back to env keys
        if not any(v not in (None, "") for v in data.values()):
            data = None
    creds = data if data is not None else ({} if own_only else _env_creds(provider))
    if not read_only:
        from backend.services.payment_guard import assert_allowed
        assert_allowed(provider, creds)
    return creds


# The one field whose presence means "this provider is actually usable" — so an
# incidental non-secret default (e.g. Stripe success/cancel URLs) doesn't make a
# provider look configured.
_ESSENTIAL = {
    "stripe": ("secret_key",), "square": ("access_token",), "sumup": ("api_key",),
    "twilio": ("account_sid",), "smtp": ("host",), "twilio_voice": ("account_sid",),
    "meta": ("page_token", "ig_token", "whatsapp_token"),
}


def _has_essential(provider: str, creds: dict) -> bool:
    keys = _ESSENTIAL.get(provider, ())
    return any(creds.get(k) for k in keys)


async def source(db: AsyncSession, org_id: str, provider: str) -> str:
    """'org' if the shop has its own keys, 'env' if only an operator default
    exists, else 'none'."""
    row = await _row(db, org_id, provider)
    if row and row.active:
        try:
            if _has_essential(provider, _decrypt(row.data_enc, provider)):
                return "org"
        except CredentialsUnreadable:
            return "unreadable"
    return "env" if _has_essential(provider, _env_creds(provider)) else "none"


# ── Writes ───────────────────────────────────────────────────────────────────
async def set_creds(db: AsyncSession, org_id: str, provider: str, data: dict) -> None:
    if not _fields(provider):
        raise ValueError(f"Unknown provider '{provider}'")
    # keep only known fields; drop blanks so a cleared field falls back to env
    clean = {f["key"]: str(data[f["key"]]).strip() for f in _fields(provider)
             if f["key"] in data and str(data.get(f["key"]) or "").strip() != ""}
    # F4: live payment credentials are refused unless live payments are switched on in production.
    from backend.services.payment_guard import assert_allowed
    assert_allowed(provider, clean)
    row = await _row(db, org_id, provider)
    if not row:
        row = OrgIntegrationCredential(org_id=org_id, provider=provider)
        db.add(row)
    row.data_enc = _encrypt(clean)
    row.active = True
    await db.commit()


async def delete_creds(db: AsyncSession, org_id: str, provider: str) -> None:
    row = await _row(db, org_id, provider)
    if row:
        await db.delete(row)
        await db.commit()


# ── Status (masked, safe for the client) ─────────────────────────────────────
async def status(db: AsyncSession, org_id: str) -> dict:
    out = {}
    for provider, fields in PROVIDER_FIELDS.items():
        row = await _row(db, org_id, provider)
        try:
            stored = _decrypt(row.data_enc, provider) if row else {}
        except CredentialsUnreadable:
            stored = {}
        src = await source(db, org_id, provider)
        out[provider] = {
            "source": src,                       # org | env | none | unreadable
            "configured": src not in ("none", "unreadable"),
            "fields": [
                {"key": f["key"], "label": f["label"], "secret": bool(f.get("secret")),
                 "set": f["key"] in stored and stored[f["key"]] != "",
                 "value": ("••••" if f.get("secret") else stored.get(f["key"], "")) if f["key"] in stored else ""}
                for f in fields
            ],
        }
    return out
