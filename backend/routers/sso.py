"""SSO groundwork — per-org SAML/OIDC identity-provider config + discovery.

Config + email-domain routing are real and testable. The actual assertion/token
exchange is a gated seam (needs a live IdP), so `/authorize` returns the provider
redirect a real flow would use rather than completing sign-in here.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.db.database import get_db
from backend.models import Employee, SsoConnection
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


def _ser(s: SsoConnection, *, redact=True) -> dict:
    return {"id": s.id, "protocol": s.protocol, "display_name": s.display_name,
            "issuer": s.issuer, "client_id": s.client_id,
            "client_secret": ("***" if s.client_secret and redact else s.client_secret),
            "metadata_url": s.metadata_url, "email_domain": s.email_domain,
            "enabled": s.enabled}


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
        # One shop per domain: otherwise a second shop could claim yours and intercept the
        # "Sign in with SSO" routing for your staff (and discover() would 500 on duplicates).
        taken = (await db.execute(select(SsoConnection.id).where(
            SsoConnection.email_domain == domain, SsoConnection.org_id != current.org_id))).first()
        if taken:
            raise HTTPException(409, f"{domain} is already linked to another account")
    from backend.services.crypto import encrypt
    conn = SsoConnection(org_id=current.org_id, **{**data.model_dump(), "email_domain": domain,
                                                     "client_secret": encrypt(data.client_secret)})
    db.add(conn)
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
        SsoConnection.email_domain == domain, SsoConnection.enabled == True)  # noqa: E712
        .order_by(SsoConnection.id).limit(1))).scalar_one_or_none()
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
        SsoConnection.id == conn_id, SsoConnection.enabled == True))).scalar_one_or_none()  # noqa: E712
    if not conn:
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
