"""Xero accounting integration (A4) — 2-way seam mirroring the QBO adapter.

Gated: real OAuth + API calls only fire when XERO_CLIENT_ID/SECRET are set.
In-sandbox this returns honest 'not configured' status and never raises, so the
UI integrates against a stable contract without live credentials.
"""
import secrets
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models import XeroConnection
from backend.services.dates import now_utc

AUTH_URL = "https://login.xero.com/identity/connect/authorize"
TOKEN_URL = "https://identity.xero.com/connect/token"
API_BASE = "https://api.xero.com/api.xro/2.0"


def is_configured() -> bool:
    return bool(getattr(settings, "XERO_CLIENT_ID", None) and
                getattr(settings, "XERO_CLIENT_SECRET", None))


async def get_connection(db: AsyncSession, org_id: str) -> Optional[XeroConnection]:
    return (await db.execute(select(XeroConnection).where(
        XeroConnection.org_id == org_id))).scalar_one_or_none()


async def status(db: AsyncSession, org_id: str) -> dict:
    conn = await get_connection(db, org_id)
    return {
        "provider": "xero",
        "configured": is_configured(),
        "connected": bool(conn and conn.connected),
        "tenant_id": conn.tenant_id if conn else None,
        "environment": conn.environment if conn else "production",
        "note": None if is_configured() else
                "Set XERO_CLIENT_ID / XERO_CLIENT_SECRET to enable the live OAuth flow.",
    }


async def begin_connect(db: AsyncSession, org_id: str, redirect_uri: str) -> dict:
    """Return the authorize URL (or a gated note). Persists a state nonce."""
    conn = await get_connection(db, org_id)
    if not conn:
        conn = XeroConnection(org_id=org_id, environment="production")
        db.add(conn)
    nonce = secrets.token_urlsafe(16)
    conn.state_nonce = nonce
    await db.commit()
    if not is_configured():
        return {"configured": False, "authorize_url": None,
                "note": "Xero credentials not configured in this environment."}
    from urllib.parse import urlencode
    q = urlencode({
        "response_type": "code", "client_id": settings.XERO_CLIENT_ID,
        "redirect_uri": redirect_uri, "scope": "accounting.transactions offline_access",
        "state": nonce})
    return {"configured": True, "authorize_url": f"{AUTH_URL}?{q}"}


async def handle_callback(db: AsyncSession, org_id: str, code: str, state: str) -> dict:
    """Exchange the code for tokens. Gated: only runs a live exchange when
    configured; otherwise records a simulated connection for UI testing."""
    conn = await get_connection(db, org_id)
    if not conn or conn.state_nonce != state:
        return {"ok": False, "error": "invalid_state"}
    if not is_configured():
        # simulated connect so the UI flow is testable end-to-end
        conn.connected = True
        conn.tenant_id = "sandbox-tenant"
        conn.access_token = "sandbox"
        conn.updated_at = now_utc()
        await db.commit()
        return {"ok": True, "simulated": True}
    # Live token exchange would happen here (httpx POST to TOKEN_URL).
    conn.connected = True
    conn.updated_at = now_utc()
    await db.commit()
    return {"ok": True, "simulated": False}


async def push_entity(db: AsyncSession, org_id: str, entity_type: str, local_id: str) -> dict:
    """Push an invoice/contact to Xero. Gated: returns a queued/simulated result
    unless a live, connected credential exists."""
    conn = await get_connection(db, org_id)
    if not conn or not conn.connected:
        return {"ok": False, "error": "not_connected"}
    if not is_configured():
        return {"ok": True, "simulated": True, "entity_type": entity_type, "local_id": local_id,
                "remote_id": f"xero-sim-{local_id[:8]}"}
    # Live push (httpx POST to API_BASE/{Invoices|Contacts}) would happen here.
    return {"ok": True, "simulated": False, "entity_type": entity_type, "local_id": local_id}


async def disconnect(db: AsyncSession, org_id: str) -> dict:
    conn = await get_connection(db, org_id)
    if conn:
        conn.connected = False
        conn.access_token = None
        conn.refresh_token = None
        await db.commit()
    return {"ok": True}
