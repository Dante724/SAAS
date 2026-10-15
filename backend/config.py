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
    APP_NAME: str = "RepairOS"
    ENVIRONMENT: str = "development"          # development|staging|production|test
    SECRET_KEY: str = "dev-secret-key-change-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440
    DATABASE_URL: str = "sqlite+aiosqlite:///./repairos.db"

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

    # -- Meta messaging (Messenger / Instagram / WhatsApp Cloud) — absent -> simulated --
    # One webhook verify token for all Meta channels; per-channel access tokens.
    META_VERIFY_TOKEN: str = ""          # echoed back on webhook subscription (GET hub.challenge)
    META_APP_SECRET: str = ""            # (reserved) X-Hub-Signature validation
    META_PAGE_TOKEN: str = ""            # Facebook Page — Messenger send
    META_IG_TOKEN: str = ""             # Instagram — Direct send (falls back to page token)
    WHATSAPP_TOKEN: str = ""            # WhatsApp Cloud API access token
    WHATSAPP_PHONE_ID: str = ""         # WhatsApp Cloud phone-number id

    # -- Payments (Stripe) — absent keys -> simulated gateway --
    STRIPE_SECRET_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""
    STRIPE_SUCCESS_URL: str = "https://app.repairos.dev/pay/success"
    STRIPE_CANCEL_URL: str = "https://app.repairos.dev/pay/cancel"

    # -- Square (absent -> not selectable; per-org provider chooses at runtime) --
    SQUARE_ACCESS_TOKEN: str = ""
    SQUARE_LOCATION_ID: str = ""
    SQUARE_ENV: str = "sandbox"                # sandbox | production
    SQUARE_WEBHOOK_SIGNATURE_KEY: str = ""

    # -- SumUp --
    SUMUP_API_KEY: str = ""
    SUMUP_MERCHANT_CODE: str = ""

    # -- QuickBooks Online --
    QBO_CLIENT_ID: str = ""
    QBO_CLIENT_SECRET: str = ""
    QBO_REDIRECT_URI: str = "https://app.repairos.dev/api/integrations/qbo/callback"
    QBO_ENVIRONMENT: str = "sandbox"

    # -- AI (Anthropic) — absent key -> deterministic stub --
    ANTHROPIC_API_KEY: str = ""
    AI_MODEL: str = "claude-sonnet-4-6"

    # ── B3: media storage ─────────────────────────────────────────────────
    MEDIA_BACKEND: str = "db"          # db | local | s3  (db = legacy inline; local/s3 = offloaded)
    MEDIA_MAX_BYTES: int = 8 * 1024 * 1024   # Build 144cq: hard cap on any single upload (8 MB)
    MEDIA_LOCAL_DIR: str = "/tmp/repairos-media"
    MEDIA_S3_BUCKET: str = ""
    MEDIA_S3_REGION: str = ""
    MEDIA_S3_PREFIX: str = "media/"

    # ── A6: email OAuth connectors (gated — inert until set) ───────────────
    GMAIL_CLIENT_ID: str = ""
    GMAIL_CLIENT_SECRET: str = ""
    OUTLOOK_CLIENT_ID: str = ""
    OUTLOOK_CLIENT_SECRET: str = ""
    EMAIL_OAUTH_REDIRECT: str = "http://localhost:8000/api/integrations/email/callback"

    # ── Token encryption at rest (OAuth access/refresh tokens) ─────────────
    # A urlsafe-base64 Fernet key. Unset (dev/tests) → tokens stored as-is;
    # decryption tolerates plaintext so rows survive turning encryption on/off.
    TOKEN_ENCRYPTION_KEY: str = ""

    # -- Web Push (VAPID) — absent keys -> notifications stored, push simulated --
    VAPID_PUBLIC_KEY: str = ""
    VAPID_PRIVATE_KEY: str = ""
    XERO_CLIENT_ID: str = ""
    XERO_CLIENT_SECRET: str = ""
    VAPID_CLAIMS_EMAIL: str = "admin@repairos.dev"

    # -- Identifier lookups (VIN is free via NHTSA; IMEI TAC needs a provider) --
    VIN_LOOKUP_LIVE: bool = True          # attempt NHTSA vPIC enrichment (keyless, best-effort)
    IMEI_LOOKUP_API_KEY: str = ""         # absent -> structural validation only

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
            if _meta_configured and not self.META_APP_SECRET:
                import logging
                logging.getLogger(__name__).warning(
                    "META_APP_SECRET is not set in production but a Meta channel appears "
                    "configured; the /api/inbox/webhook/meta endpoint will accept unsigned "
                    "posts. Set META_APP_SECRET to enable X-Hub-Signature-256 verification."
                )
        return self


settings = Settings()
