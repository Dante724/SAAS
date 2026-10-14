"""SSO groundwork — per-org SAML/OIDC identity-provider config + discovery.

Config + email-domain routing are real and testable. The actual assertion/token
exchange is a gated seam (needs a live IdP), so `/authorize` returns the provider
redirect a real flow would use rather than completing sign-in here.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.models import Employee, SsoConnection
from backend.services.auth import require_perm

router = APIRouter(prefix="/api/sso", tags=["sso"])


class SsoIn(BaseModel):
    protocol: str = "oidc"        # oidc|saml
    display_name: str = ""
    issuer: Optional[str] = None
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    metadata_url: Optional[str] = None
    email_domain: Optional[str] = None
    enabled: bool = False


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
    conn = SsoConnection(org_id=current.org_id, **data.model_dump())
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
        SsoConnection.email_domain == domain, SsoConnection.enabled == True))).scalar_one_or_none()  # noqa: E712
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
    if conn.protocol == "oidc" and conn.issuer and conn.client_id:
        from urllib.parse import urlencode
        params = urlencode({"client_id": conn.client_id, "response_type": "code",
                            "scope": "openid email profile", "redirect_uri": redirect_uri})
        return {"ready": True, "authorization_url": f"{conn.issuer.rstrip('/')}/authorize?{params}"}
    return {"ready": False, "reason": "Provider not fully configured (issuer/client_id)"}
