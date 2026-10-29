"""Centralised application configuration.

All runtime configuration is sourced from environment variables (or a local
.env file in development). Secrets never live in code. In production the app
refuses to boot with an unset/placeholder SECRET_KEY (fail-closed).
"""


from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_PLACEHOLDER_SECRETS = {
    "",
    "dev-secret-key-change-in-production",
    "change-this-to-a-long-random-string-at-least-32-chars",
}


def _valid_fernet_keys(raw: str) -> bool:
    keys = [k.strip() for k in (raw or "").split(",") if k.strip()]
    if not keys:
        return False
    try:
        from cryptography.fernet import Fernet
        for k in keys:
            Fernet(k.encode())
    except Exception:
        return False
    return True


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # -- Core --
    APP_NAME: str = "Tekly"
    ENVIRONMENT: str = "development"          # development|staging|production|test
    SECRET_KEY: str = "dev-secret-key-change-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440
    DATABASE_URL: str = "sqlite+aiosqlite:///./repairos.db"
    # Postgres pool + guards (per worker process: pool_size + max_overflow connections at most,
    # so workers x that must stay under the server's max_connections).
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 10
    DB_POOL_TIMEOUT_S: int = 10                # wait this long for a free connection, then error (not hang)
    DB_POOL_RECYCLE_S: int = 1800
    DB_STATEMENT_TIMEOUT_MS: int = 15000       # Postgres cancels any single statement running longer; 0 = off
    DB_IDLE_IN_TX_TIMEOUT_MS: int = 60000      # ends a session left idle inside a transaction; 0 = off

    # -- Operational toggles --
    SEED_DEMO: bool = False                    # seed demo data on startup (dev only)
    RUN_MIGRATIONS_ON_STARTUP: bool = False    # dev convenience; prod uses `alembic upgrade head`
    CORS_ORIGINS: str = "*"                     # csv allowlist; "*" disallowed in production
    LOG_LEVEL: str = "INFO"
    REDIS_URL: str = ""                        # set -> shared rate-limit store across instances (else in-process)
    # How many reverse proxies (load balancer, CDN…) sit in front of the app. 0 = clients connect
    # directly, so X-Forwarded-For is ignored (a client could write anything there). N = trust the
    # address appended by the N-th proxy from the right. Alternatively run uvicorn with
    # --proxy-headers --forwarded-allow-ips=<proxy ip> and leave this at 0.
    TRUSTED_PROXY_HOPS: int = 0
    # Global request ceilings per minute (core.observability.RateLimitMiddleware). Static files are
    # never counted. Signed-in requests count per user; anonymous ones per client IP.
    RATE_LIMIT_USER_PER_MIN: int = 600
    RATE_LIMIT_IP_PER_MIN: int = 600
    LOG_JSON: bool = False                      # structured JSON logs (recommended in prod)

    # -- Notification providers (absent keys -> simulated/logged) --
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = ""
    SMTP_USE_TLS: bool = True
    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: str = ""
    TWILIO_FROM: str = ""
    TWILIO_WHATSAPP_FROM: str = ""
    # D1: a shop's live Twilio voice credentials are refused unless this is true AND
    # ENVIRONMENT=production. Test credentials (magic numbers, nothing dials) always work.
    ALLOW_LIVE_TELEPHONY: bool = False
    # WS-C: the Aircall adapter stays off (webhook 404s, settings refuse) until the owner has an
    # Aircall account and turns this on.
    AIRCALL_ENABLED: bool = False

    # -- Meta messaging (Messenger / Instagram / WhatsApp Cloud) — absent -> simulated --
    # One webhook verify token for all Meta channels; per-channel access tokens.
    META_VERIFY_TOKEN: str = ""          # echoed back on webhook subscription (GET hub.challenge)
    META_APP_SECRET: str = ""            # verifies X-Hub-Signature-256 on Meta webhooks
    META_PAGE_TOKEN: str = ""            # Facebook Page — Messenger send
    META_IG_TOKEN: str = ""             # Instagram — Direct send (falls back to page token)
    WHATSAPP_TOKEN: str = ""            # WhatsApp Cloud API access token
    WHATSAPP_PHONE_ID: str = ""         # WhatsApp Cloud phone-number id

    # -- Payments (Stripe) — absent keys -> simulated gateway --
    STRIPE_SECRET_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""
    # Where Stripe Checkout returns the customer. Empty (default) = back to their own pay page,
    # /pay/{intent_id} on PUBLIC_BASE_URL, which re-checks with the provider and shows "paid".
    STRIPE_SUCCESS_URL: str = ""
    STRIPE_CANCEL_URL: str = ""

    # -- Square (absent -> not selectable; per-org provider chooses at runtime) --
    SQUARE_ACCESS_TOKEN: str = ""
    SQUARE_LOCATION_ID: str = ""
    SQUARE_ENV: str = "sandbox"                # sandbox | production
    SQUARE_WEBHOOK_SIGNATURE_KEY: str = ""

    # -- Live-money guardrail (F4) --
    # Live keys (Stripe sk_live_/rk_live_, Square production) are refused unless this is true AND
    # ENVIRONMENT=production. Everywhere else only test/sandbox credentials can move money.
    ALLOW_LIVE_PAYMENTS: bool = False

    # -- SumUp --
    SUMUP_API_KEY: str = ""
    SUMUP_MERCHANT_CODE: str = ""

    # -- QuickBooks Online --
    QBO_CLIENT_ID: str = ""
    QBO_CLIENT_SECRET: str = ""
    QBO_REDIRECT_URI: str = ""                 # empty = {PUBLIC_BASE_URL}/api/integrations/qbo/callback
    QBO_ENVIRONMENT: str = "sandbox"

    # -- AI (Anthropic) — absent key -> deterministic stub --
    ANTHROPIC_API_KEY: str = ""
    AI_MODEL: str = "claude-sonnet-4-6"

    # ── B3: media storage ─────────────────────────────────────────────────
    MEDIA_BACKEND: str = "db"          # db | local | s3  (db = legacy inline; local/s3 = offloaded)
    MEDIA_MAX_BYTES: int = 8 * 1024 * 1024   # Build 144cq: hard cap on any single upload (8 MB)
    # Whole request body cap (base64 of an 8 MB upload is ~11 MB of JSON). 0 disables.
    MAX_REQUEST_BYTES: int = 16 * 1024 * 1024
    MEDIA_LOCAL_DIR: str = "/tmp/repairos-media"
    MEDIA_S3_BUCKET: str = ""
    MEDIA_S3_REGION: str = ""
    MEDIA_S3_PREFIX: str = "media/"

    # ── A6: email OAuth connectors (gated — inert until set) ───────────────
    GMAIL_CLIENT_ID: str = ""
    GMAIL_CLIENT_SECRET: str = ""
    OUTLOOK_CLIENT_ID: str = ""
    OUTLOOK_CLIENT_SECRET: str = ""
    EMAIL_OAUTH_REDIRECT: str = ""             # empty = {PUBLIC_BASE_URL}/api/integrations/email/callback

    # ── Token encryption at rest (OAuth access/refresh tokens) ─────────────
    # A urlsafe-base64 Fernet key. Unset (dev/tests) → tokens stored as-is;
    # decryption tolerates plaintext so rows survive turning encryption on/off.
    TOKEN_ENCRYPTION_KEY: str = ""

    # -- Web Push (VAPID) — absent keys -> notifications stored, push simulated --
    VAPID_PUBLIC_KEY: str = ""
    VAPID_PRIVATE_KEY: str = ""
    XERO_CLIENT_ID: str = ""
    XERO_CLIENT_SECRET: str = ""
    VAPID_CLAIMS_EMAIL: str = ""               # empty = SMTP_FROM

    # -- Identifier lookups (VIN is free via NHTSA; IMEI TAC needs a provider) --
    VIN_LOOKUP_LIVE: bool = True          # attempt NHTSA vPIC enrichment (keyless, best-effort)
    IMEI_LOOKUP_API_KEY: str = ""         # absent -> structural validation only

    # -- Error tracking (Sentry protocol). Empty DSN = off. --
    SENTRY_DSN: str = ""
    SENTRY_TRACES_SAMPLE_RATE: float = 0.0
    RELEASE: str = ""                       # e.g. the git SHA, set by the deploy

    # -- Prometheus scrape token for /metrics (required to expose it when deployed) --
    METRICS_TOKEN: str = ""

    # -- Auth refresh --
    REFRESH_TOKEN_EXPIRE_MINUTES: int = 43200   # 30 days

    # -- Public base URL for shareable links (payment pages, portals). Empty -> relative. --
    PUBLIC_BASE_URL: str = ""

    # -- Build 144ce: bootstrap platform-admin account for the /admin portal. --
    PLATFORM_ADMIN_EMAIL: str = ""
    PLATFORM_ADMIN_PASSWORD: str = ""

    # -- Derived helpers --
    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT.lower() == "production"

    @property
    def is_deployed(self) -> bool:
        """Production *and* staging: anything reachable by real users gets the fail-closed
        config checks and hardening (no demo seed, no simulated payments, no default admin,
        no runtime DDL, hidden API docs). Only development and test are relaxed."""
        return self.ENVIRONMENT.lower() in ("production", "staging")

    @property
    def public_base(self) -> str:
        """PUBLIC_BASE_URL without a trailing slash; local dev falls back to the dev server."""
        return (self.PUBLIC_BASE_URL or "http://localhost:8000").rstrip("/")

    @property
    def is_sqlite(self) -> bool:
        return self.DATABASE_URL.startswith("sqlite")

    @property
    def cors_origin_list(self) -> list[str]:
        if self.CORS_ORIGINS.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @field_validator("ENVIRONMENT")
    @classmethod
    def _env_known(cls, v: str) -> str:
        if v.lower() not in {"development", "staging", "production", "test"}:
            raise ValueError("ENVIRONMENT must be development|staging|production|test")
        return v

    @model_validator(mode="after")
    def _derive_urls(self) -> "Settings":
        """OAuth callbacks follow PUBLIC_BASE_URL unless set explicitly (they used to default to
        the old app.repairos.dev domain and localhost)."""
        if not self.QBO_REDIRECT_URI:
            self.QBO_REDIRECT_URI = self.public_base + "/api/integrations/qbo/callback"
        if not self.EMAIL_OAUTH_REDIRECT:
            self.EMAIL_OAUTH_REDIRECT = self.public_base + "/api/integrations/email/callback"
        if not self.VAPID_CLAIMS_EMAIL:
            self.VAPID_CLAIMS_EMAIL = self.SMTP_FROM or "admin@localhost"
        return self

    @model_validator(mode="after")
    def _fail_closed_in_prod(self) -> "Settings":
        """Refuse to run production or staging with placeholder secrets, wildcard CORS, or
        secrets-at-rest left unencrypted."""
        if self.is_deployed:
            if self.SECRET_KEY in _PLACEHOLDER_SECRETS or len(self.SECRET_KEY) < 32:
                raise ValueError(
                    "SECRET_KEY must be a strong (>=32 char) value in production. "
                    "Generate: python -c \"import secrets; print(secrets.token_urlsafe(48))\""
                )
            if "*" in self.cors_origin_list:
                raise ValueError("CORS_ORIGINS must be an explicit allowlist in production (no '*').")
            # The default DATABASE_URL is a local SQLite file: a deploy that forgot to set it booted
            # on an empty database (runtime DDL is off when deployed) and /api/health still said ok.
            if self.is_sqlite:
                raise ValueError("DATABASE_URL must point at Postgres in production/staging, not SQLite.")
            # Emailed links (verify, reset, invites, portals, pay links) and provider callbacks are
            # built from PUBLIC_BASE_URL; empty made them relative and broken in every inbox.
            from urllib.parse import urlparse
            _base = urlparse(self.PUBLIC_BASE_URL or "")
            if not (_base.scheme == "https" or (_base.scheme == "http" and _base.hostname in ("localhost", "127.0.0.1"))) \
                    or not _base.hostname:
                raise ValueError("PUBLIC_BASE_URL must be the app's public https:// address in production/staging.")
            # Password reset, verification and invites go out through the platform SMTP account;
            # without it they were only printed to the log and reported as sent.
            if not (self.SMTP_HOST and self.SMTP_FROM):
                raise ValueError("SMTP_HOST and SMTP_FROM must be set in production/staging "
                                 "(password reset and verification emails).")
            _stale = [k for k in ("STRIPE_SUCCESS_URL", "STRIPE_CANCEL_URL", "QBO_REDIRECT_URI",
                                  "EMAIL_OAUTH_REDIRECT", "PUBLIC_BASE_URL")
                      if "repairos.dev" in (getattr(self, k) or "")]
            if _stale:
                raise ValueError(f"{', '.join(_stale)} still point at the old repairos.dev domain.")
            # OAuth tokens and shop integration credentials are encrypted with this key. Without
            # it, OAuth tokens were stored in plaintext and shop credentials fell back to a key
            # derived from SECRET_KEY (the JWT signing key).
            if not _valid_fernet_keys(self.TOKEN_ENCRYPTION_KEY):
                raise ValueError(
                    "TOKEN_ENCRYPTION_KEY must be set in production/staging (comma-separated Fernet "
                    "keys, newest first). Generate: python -c \"from cryptography.fernet import "
                    "Fernet; print(Fernet.generate_key().decode())\""
                )
            # Webhook signing secrets must be set for any payment provider actually in
            # use, so inbound provider callbacks can't be forged. Gate each only on its
            # provider being configured — don't break shops that don't use it.
            if self.STRIPE_SECRET_KEY and not self.STRIPE_WEBHOOK_SECRET:
                raise ValueError(
                    "STRIPE_WEBHOOK_SECRET must be set in production when Stripe is "
                    "configured (STRIPE_SECRET_KEY present), to verify webhook signatures."
                )
            if self.SQUARE_ACCESS_TOKEN and not self.SQUARE_WEBHOOK_SIGNATURE_KEY:
                raise ValueError(
                    "SQUARE_WEBHOOK_SIGNATURE_KEY must be set in production when Square is "
                    "configured (SQUARE_ACCESS_TOKEN present), to verify webhook signatures."
                )
            # Meta has no single "enabled" flag and plenty of shops don't use it, so a
            # missing app secret is a warning rather than a hard boot failure. When any
            # Meta channel is configured, the unsigned inbound webhook is a real risk.
            _meta_configured = any([
                self.META_VERIFY_TOKEN, self.META_PAGE_TOKEN,
                self.META_IG_TOKEN, self.WHATSAPP_TOKEN,
            ])
            import logging
            import os
            _log = logging.getLogger(__name__)
            # Rate limits (login lockouts included) count in-process without Redis, so N workers
            # or instances allow N times the limit.
            if not self.REDIS_URL and int(os.environ.get("WEB_CONCURRENCY", "1") or 1) > 1:
                _log.warning("REDIS_URL is not set but WEB_CONCURRENCY > 1: every worker keeps its own "
                             "rate-limit counters. Set REDIS_URL so limits are shared.")
            if self.MEDIA_BACKEND == "local" and self.MEDIA_LOCAL_DIR.startswith("/tmp"):
                _log.warning("MEDIA_BACKEND=local with MEDIA_LOCAL_DIR under /tmp: uploaded photos and "
                             "files are lost when the container restarts. Use MEDIA_BACKEND=s3 or a volume.")
            # Behind a load balancer with neither setting changed, every anonymous request looks
            # like it comes from the balancer, so they all share one rate-limit bucket.
            if self.TRUSTED_PROXY_HOPS == 0 and os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1") == "127.0.0.1":
                _log.warning("TRUSTED_PROXY_HOPS=0 and FORWARDED_ALLOW_IPS=127.0.0.1: if a load balancer "
                             "sits in front, set one of them so rate limits see real client IPs.")
            if _meta_configured and not self.META_APP_SECRET:
                import logging
                logging.getLogger(__name__).warning(
                    "META_APP_SECRET is not set in production but a Meta channel appears "
                    "configured; /api/chats/webhook/meta will refuse every post (signatures fail "
                    "closed). Set META_APP_SECRET to receive Meta messages."
                )
        return self


settings = Settings()
