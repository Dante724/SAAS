"""SSO groundwork — per-org SAML/OIDC identity-provider config + discovery.

Config + email-domain routing are real and testable. The actual assertion/token
exchange is a gated seam (needs a live IdP), so `/authorize` returns the provider
redirect a real flow would use rather than completing sign-in here.

Audit SEC-07: a shop claiming an email domain must prove it owns it before that domain routes
anyone anywhere. The connection carries a token; the shop publishes it as a DNS TXT record on the
domain and calls `/config/{id}/verify-domain`. Until then `discover` ignores the connection and
`authorize` refuses it. An unproven claim blocks nobody, so a squatter can't lock the real owner
out; once one shop proves a domain, no other shop can claim or prove it.
"""
import logging
import secrets
from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.db.database import get_db
from backend.models import Employee, SsoConnection
from backend.services.audit import record as audit_record
from backend.services.auth import require_perm

router = APIRouter(prefix="/api/sso", tags=["sso"])


class SsoIn(BaseModel):
    protocol: str = "oidc"        # oidc|saml
    display_name: str = ""
    issuer: Optional[str] = None
    client_id: Optional[str] = None
    client_secret: Optional[str] = Field(None, max_length=120)
    metadata_url: Optional[str] = None
    email_domain: Optional[str] = None
    enabled: bool = False


# Consumer mailbox providers: nobody may claim these for SSO routing — every user of the
# provider would be offered (or sent to) one shop's identity provider.
_PUBLIC_DOMAINS = {
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "msn.com",
    "yahoo.com", "ymail.com", "icloud.com", "me.com", "mac.com", "aol.com", "proton.me",
    "protonmail.com", "gmx.com", "gmx.de", "mail.com", "yandex.com", "yandex.ru", "zoho.com",
    "rediffmail.com", "qq.com", "163.com",
}


def _allowed_redirect(uri: str) -> bool:
    """The IdP sends the authorization code here, so it must be one of our own origins."""
    from urllib.parse import urlsplit
    target = urlsplit(uri)
    if target.scheme not in ("https", "http") or not target.netloc:
        return False
    origins = [o for o in [settings.PUBLIC_BASE_URL, *settings.cors_origin_list] if o and o != "*"]
    for o in origins:
        base = urlsplit(o)
        if (base.scheme, base.netloc) == (target.scheme, target.netloc):
            return True
    return False


TXT_PREFIX = "tekly-sso-verification="
log = logging.getLogger("tekly.sso")

# Re-review N7: a proof is not forever. The domain can change hands or the record can be removed,
# so a verified connection is re-checked in DNS once it is RECHECK_AFTER old (the daily sweep
# below, or the shop pressing Verify again), and stops routing anything once it is VERIFY_TTL old
# without a fresh check. A record found missing on a re-check revokes the proof at once.
VERIFY_TTL = timedelta(days=30)
RECHECK_AFTER = timedelta(days=7)
VERIFY_LIMIT = 10              # verify-domain presses per connection per hour (each is outbound DNS)
VERIFY_ORG_LIMIT = 30          # and per shop per hour


async def lookup_txt(domain: str) -> list[str]:
    """The domain's own TXT records as plain strings ([] when there are none or DNS can't answer).
    A long record arrives as several <=255-byte strings; they are joined back into one.

    Re-review N7: an answer reached through a CNAME is ignored. The proof must be published on the
    claimed name itself: a dangling CNAME (sso.acme.test -> an abandoned host someone else now
    controls) would otherwise let that someone prove acme's subdomain."""
    import dns.asyncresolver
    import dns.exception
    import dns.name
    try:
        answer = await dns.asyncresolver.resolve(domain, "TXT", lifetime=8)
    except (dns.exception.DNSException, OSError):
        return []
    asked = dns.name.from_text(domain)
    if getattr(answer, "canonical_name", asked) != asked:
        log.info("sso: TXT for a claimed domain came through a CNAME; ignored")
        return []
    return [b"".join(r.strings).decode("utf-8", "replace") for r in answer]


def _fresh_since():
    from backend.services.dates import now_utc
    return now_utc() - VERIFY_TTL


def proven(conn) -> bool:
    """Is this connection's email domain proven, and the proof still fresh?"""
    return conn.domain_verified_at is not None and conn.domain_verified_at >= _fresh_since()


def email_matches_connection(conn, email: str) -> bool:
    """The check the OIDC/SAML callback MUST make before signing anyone in (audit SEC-07,
    re-review N7): the email the identity provider asserts is exactly in the connection's proven
    domain (no subdomains, no look-alikes), and that proof is still fresh. Otherwise any IdP a shop
    points at could assert any address and sign in as someone of another shop.

    TODO(sso-callback): the callback does not exist yet (`authorize` stops at the IdP redirect).
    When it ships it must call this with the verified `email` claim (and require
    `email_verified`), refuse a mismatch, and only then look up the employee in `conn.org_id`."""
    email = (email or "").strip().lower()
    if email.count("@") != 1 or not getattr(conn, "enabled", False) or not proven(conn):
        return False
    return email.rsplit("@", 1)[1] == (conn.email_domain or "").lower()


def _verification(s: SsoConnection) -> Optional[dict]:
    if not s.email_domain:
        return None
    # A connection from before SEC-07 has no token yet: it is minted by verify-domain (a write),
    # never by reading the settings page (re-review N7: GET /config used to mint and commit).
    return {"record_type": "TXT", "host": s.email_domain,
            "value": f"{TXT_PREFIX}{s.domain_token}" if s.domain_token else None,
            "verified": proven(s),
            "verified_at": s.domain_verified_at.isoformat() if s.domain_verified_at else None,
            "expires_at": (s.domain_verified_at + VERIFY_TTL).isoformat() if s.domain_verified_at else None}


def _active():
    """Connections allowed to route sign-ins: enabled, with a proven email domain whose proof is
    still fresh (re-review N7)."""
    return (SsoConnection.enabled == True, SsoConnection.domain_verified_at.isnot(None),  # noqa: E712
            SsoConnection.domain_verified_at >= _fresh_since())


def _ser(s: SsoConnection, *, redact=True) -> dict:
    return {"id": s.id, "protocol": s.protocol, "display_name": s.display_name,
            "issuer": s.issuer, "client_id": s.client_id,
            "client_secret": ("***" if s.client_secret and redact else s.client_secret),
            "metadata_url": s.metadata_url, "email_domain": s.email_domain,
            "enabled": s.enabled, "domain_verification": _verification(s)}


async def _proven_elsewhere(db: AsyncSession, domain: str, org_id: str) -> bool:
    """Has another shop already proven this domain? skip_tenant_filter: the question is about
    OTHER shops' connections; only an id is read."""
    return (await db.execute(select(SsoConnection.id).where(
        SsoConnection.email_domain == domain, SsoConnection.org_id != org_id,
        SsoConnection.domain_verified_at.isnot(None))
        .execution_options(skip_tenant_filter=True))).first() is not None


@router.get("/config")
async def get_config(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    rows = (await db.execute(select(SsoConnection).where(
        SsoConnection.org_id == current.org_id))).scalars().all()
    return {"items": [_ser(s) for s in rows]}


@router.post("/config", status_code=201)
async def create_config(
    data: SsoIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    if data.protocol not in ("oidc", "saml"):
        raise HTTPException(422, "protocol must be oidc|saml")
    for url in (data.issuer, data.metadata_url):
        if url and not url.lower().startswith("https://"):
            raise HTTPException(422, "Identity provider URLs must use https://")
    domain = (data.email_domain or "").strip().lower().lstrip("@") or None
    if domain:
        if domain in _PUBLIC_DOMAINS:
            raise HTTPException(422, f"{domain} is a public mail provider and can't be routed to SSO")
        # One shop per domain, decided by proof: a domain another shop has verified can't be
        # claimed. An unverified claim blocks nobody (SEC-07: "first come wins" let a squatter
        # lock the real owner out), and routes nothing until it is verified.
        if await _proven_elsewhere(db, domain, current.org_id):
            raise HTTPException(409, f"{domain} is already linked to another account")
    from backend.services.crypto import encrypt
    conn = SsoConnection(org_id=current.org_id, **{**data.model_dump(), "email_domain": domain,
                                                     "client_secret": encrypt(data.client_secret)},
                         domain_token=secrets.token_urlsafe(24) if domain else None)
    db.add(conn)
    await db.flush()
    await audit_record(db, org_id=current.org_id, actor_id=current.id, action="sso.create",
                       entity_type="sso_connection", entity_id=conn.id,
                       diff={"fields": sorted(k for k, v in data.model_dump().items() if v not in (None, ""))})
    await db.commit()
    await db.refresh(conn)
    return _ser(conn)


@router.post("/config/{conn_id}/verify-domain")
async def verify_domain(
    conn_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    """Check the domain's DNS for this connection's TXT record; when it's there the domain is
    proven and the connection starts routing sign-ins (if enabled). Every press checks DNS again
    (re-review N7): a record still there renews the proof, one that was removed revokes it."""
    conn = (await db.execute(select(SsoConnection).where(
        SsoConnection.id == conn_id, SsoConnection.org_id == current.org_id))).scalar_one_or_none()
    if not conn:
        raise HTTPException(404, "SSO config not found")
    if not conn.email_domain:
        raise HTTPException(422, "This connection has no email domain to verify")
    # Re-review N7: each press is an outbound DNS lookup of a name the caller chose.
    from backend.services.ratelimit import aenforce
    await aenforce("sso_verify_conn", conn.id, VERIFY_LIMIT, 3600)
    await aenforce("sso_verify_org", current.org_id, VERIFY_ORG_LIMIT, 3600)
    if await _proven_elsewhere(db, conn.email_domain, current.org_id):
        raise HTTPException(409, f"{conn.email_domain} is already linked to another account")
    if not conn.domain_token:
        conn.domain_token = secrets.token_urlsafe(24)
        await db.commit()
    if not await _check_dns(db, conn, actor_id=current.id):
        await db.commit()
        want = f"{TXT_PREFIX}{conn.domain_token}"
        raise HTTPException(422, f"No TXT record \"{want}\" found on {conn.email_domain} yet. "
                                 "Add it at your DNS provider (on that exact name, not through a CNAME), "
                                 "wait for it to publish, then try again.")
    await db.commit()
    await db.refresh(conn)
    return _ser(conn)


async def _check_dns(db: AsyncSession, conn: SsoConnection, *, actor_id: Optional[str] = None) -> bool:
    """Look the proof up now: found renews `domain_verified_at`, missing revokes it (both audited
    when the state changes). Not committed. Returns whether the record was found."""
    from backend.services.dates import now_utc
    found = f"{TXT_PREFIX}{conn.domain_token}" in [t.strip() for t in await lookup_txt(conn.email_domain)]
    was = conn.domain_verified_at is not None
    if found:
        conn.domain_verified_at = now_utc()
        if not was:
            await audit_record(db, org_id=conn.org_id, actor_id=actor_id, action="sso.domain_verified",
                               entity_type="sso_connection", entity_id=conn.id,
                               diff={"email_domain": conn.email_domain})
    elif was:
        conn.domain_verified_at = None
        await audit_record(db, org_id=conn.org_id, actor_id=actor_id, action="sso.domain_unverified",
                           entity_type="sso_connection", entity_id=conn.id,
                           diff={"email_domain": conn.email_domain, "reason": "TXT record no longer found"})
    return found


async def recheck_domains(db: AsyncSession) -> int:
    """Re-check every proof older than RECHECK_AFTER (renew or revoke). Run daily by the job
    runner below; returns how many were checked. Platform-wide (no tenant): it reads and writes
    only sso_connections rows, each under its own shop's id."""
    from backend.services.dates import now_utc
    due = (await db.execute(select(SsoConnection).where(
        SsoConnection.domain_verified_at.isnot(None), SsoConnection.domain_token.isnot(None),
        SsoConnection.domain_verified_at < now_utc() - RECHECK_AFTER)
        .execution_options(skip_tenant_filter=True))).scalars().all()
    for conn in due:
        await _check_dns(db, conn)
    await db.commit()
    return len(due)


def _register_recheck() -> None:
    from backend.core.jobs import job_runner

    @job_runner.every(24 * 3600, "sso-domain-recheck", delay_first=True)
    async def _sweep() -> None:
        from backend.db.database import AsyncSessionLocal
        async with AsyncSessionLocal() as db:
            n = await recheck_domains(db)
            if n:
                log.info("sso-domain-recheck: re-checked %d domain proof(s)", n)


_register_recheck()


@router.delete("/config/{conn_id}")
async def delete_config(
    conn_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    conn = (await db.execute(select(SsoConnection).where(
        SsoConnection.id == conn_id, SsoConnection.org_id == current.org_id))).scalar_one_or_none()
    if not conn:
        raise HTTPException(404, "SSO config not found")
    await db.delete(conn)
    await audit_record(db, org_id=current.org_id, actor_id=current.id, action="sso.delete",
                       entity_type="sso_connection", entity_id=conn_id)
    await db.commit()
    return {"ok": True}


@router.get("/discover")
async def discover(
    email: str = Query(..., description="email to route to an IdP by domain"),
    db: AsyncSession = Depends(get_db),
):
    """Unauth: given an email, return the org's enabled SSO provider for that
    domain (so a login page can show 'Sign in with <IdP>'). Groundwork for
    domain-based SSO routing."""
    domain = email.split("@")[-1].lower() if "@" in email else ""
    if not domain:
        return {"sso": False}
    conn = (await db.execute(select(SsoConnection).where(
        SsoConnection.email_domain == domain, *_active())
        .order_by(SsoConnection.domain_verified_at, SsoConnection.id).limit(1))).scalar_one_or_none()
    if not conn:
        return {"sso": False}
    return {"sso": True, "protocol": conn.protocol, "display_name": conn.display_name,
            "provider_config_id": conn.id}


@router.get("/authorize/{conn_id}")
async def authorize(
    conn_id: str,
    redirect_uri: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """Gated seam: returns the IdP authorization URL a real OIDC flow would send
    the browser to. Completing the exchange requires a live IdP + secret."""
    conn = (await db.execute(select(SsoConnection).where(
        SsoConnection.id == conn_id, *_active()))).scalar_one_or_none()
    if not conn:   # unknown, disabled, or its email domain not yet proven (SEC-07)
        raise HTTPException(404, "SSO provider not found or disabled")
    if not _allowed_redirect(redirect_uri):
        raise HTTPException(422, "redirect_uri must be on this site")
    if conn.protocol == "oidc" and conn.issuer and conn.client_id:
        import secrets
        from urllib.parse import urlencode
        params = urlencode({"client_id": conn.client_id, "response_type": "code",
                            "scope": "openid email profile", "redirect_uri": redirect_uri,
                            "state": secrets.token_urlsafe(24), "nonce": secrets.token_urlsafe(24)})
        return {"ready": True, "authorization_url": f"{conn.issuer.rstrip('/')}/authorize?{params}"}
    return {"ready": False, "reason": "Provider not fully configured (issuer/client_id)"}
