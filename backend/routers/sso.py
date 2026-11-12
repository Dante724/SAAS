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
import secrets
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


async def lookup_txt(domain: str) -> list[str]:
    """The domain's TXT records as plain strings ([] when there are none or DNS can't answer).
    A long record arrives as several <=255-byte strings; they are joined back into one."""
    import dns.asyncresolver
    import dns.exception
    try:
        answer = await dns.asyncresolver.resolve(domain, "TXT", lifetime=8)
    except (dns.exception.DNSException, OSError):
        return []
    return [b"".join(r.strings).decode("utf-8", "replace") for r in answer]


def _verification(s: SsoConnection) -> Optional[dict]:
    if not s.email_domain:
        return None
    return {"record_type": "TXT", "host": s.email_domain, "value": f"{TXT_PREFIX}{s.domain_token}",
            "verified": s.domain_verified_at is not None,
            "verified_at": s.domain_verified_at.isoformat() if s.domain_verified_at else None}


def _active():
    """Connections allowed to route sign-ins: enabled, with a proven email domain."""
    return (SsoConnection.enabled == True, SsoConnection.domain_verified_at.isnot(None))  # noqa: E712


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
    fill = [s for s in rows if s.email_domain and not s.domain_token]
    for s in fill:           # connections from before SEC-07 get their token on first view
        s.domain_token = secrets.token_urlsafe(24)
    if fill:
        await db.commit()
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
    proven and the connection starts routing sign-ins (if enabled)."""
    conn = (await db.execute(select(SsoConnection).where(
        SsoConnection.id == conn_id, SsoConnection.org_id == current.org_id))).scalar_one_or_none()
    if not conn:
        raise HTTPException(404, "SSO config not found")
    if not conn.email_domain:
        raise HTTPException(422, "This connection has no email domain to verify")
    if await _proven_elsewhere(db, conn.email_domain, current.org_id):
        raise HTTPException(409, f"{conn.email_domain} is already linked to another account")
    if conn.domain_verified_at is None:
        if not conn.domain_token:
            conn.domain_token = secrets.token_urlsafe(24)
            await db.commit()
        want = f"{TXT_PREFIX}{conn.domain_token}"
        if want not in [t.strip() for t in await lookup_txt(conn.email_domain)]:
            raise HTTPException(422, f"No TXT record \"{want}\" found on {conn.email_domain} yet. "
                                     "Add it at your DNS provider, wait for it to publish, then try again.")
        from backend.services.dates import now_utc
        conn.domain_verified_at = now_utc()
        await audit_record(db, org_id=current.org_id, actor_id=current.id, action="sso.domain_verified",
                           entity_type="sso_connection", entity_id=conn.id,
                           diff={"email_domain": conn.email_domain})
        await db.commit()
        await db.refresh(conn)
    return _ser(conn)


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
