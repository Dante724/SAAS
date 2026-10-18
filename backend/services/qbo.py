"""QuickBooks Online integration seam (Phase 2C).

OAuth2 (Intuit) connect flow + two-way-capable push sync for invoices, payments
and contacts. All Intuit API calls are gated on QBO_CLIENT_ID/SECRET and wrapped
so they never raise into the request; every sync attempt is recorded in
IntegrationSyncLog and successful pushes create an IntegrationMapping. Without
credentials the connection is unavailable and sync attempts are logged 'skipped'.
"""
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urlencode

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models import (
    QboConnection, IntegrationMapping, IntegrationSyncLog, Invoice, Payment, Client,
)
from backend.services.crypto import decrypt as _dec
from backend.services.crypto import encrypt as _enc
from backend.services.money import q2

AUTH_BASE = "https://appcenter.intuit.com/connect/oauth2"
TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
SCOPE = "com.intuit.quickbooks.accounting"


def _sign_state(conn_id: str, nonce: str) -> str:
    """Signed OAuth state so the (Bearer-less) callback can recover org context
    and reject tampering: ``{conn_id}:{nonce}:{hmac(SECRET_KEY)}``."""
    body = f"{conn_id}:{nonce}"
    sig = hmac.new(settings.SECRET_KEY.encode(), body.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{body}:{sig}"


def _parse_state(state: str):
    """Return (conn_id, nonce) if the signature verifies, else (None, None)."""
    try:
        conn_id, nonce, sig = state.split(":", 2)
    except ValueError:
        return None, None
    body = f"{conn_id}:{nonce}"
    expect = hmac.new(settings.SECRET_KEY.encode(), body.encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(sig, expect):
        return None, None
    return conn_id, nonce


def is_configured() -> bool:
    return bool(settings.QBO_CLIENT_ID and settings.QBO_CLIENT_SECRET)


async def get_connection(db: AsyncSession, org_id: str) -> Optional[QboConnection]:
    return (await db.execute(select(QboConnection).where(
        QboConnection.org_id == org_id))).scalar_one_or_none()


async def begin_connect(db: AsyncSession, org_id: str) -> dict:
    if not is_configured():
        from fastapi import HTTPException
        raise HTTPException(400, "QuickBooks is not configured (set QBO_CLIENT_ID/SECRET)")
    conn = await get_connection(db, org_id)
    if not conn:
        conn = QboConnection(org_id=org_id, environment=settings.QBO_ENVIRONMENT)
        db.add(conn)
    conn.state_nonce = secrets.token_urlsafe(16)
    await db.commit()
    state = _sign_state(conn.id, conn.state_nonce)
    params = {"client_id": settings.QBO_CLIENT_ID, "response_type": "code", "scope": SCOPE,
              "redirect_uri": settings.QBO_REDIRECT_URI, "state": state}
    return {"auth_url": f"{AUTH_BASE}?{urlencode(params)}", "state": state}


async def complete_connect(db: AsyncSession, code: str, realm_id: str, state: str) -> QboConnection:
    """Finish OAuth from the provider redirect. No Bearer token is present on this
    request — org/user context is recovered from the signed ``state`` and matched
    against the stored per-connection nonce (CSRF)."""
    from fastapi import HTTPException
    conn_id, nonce = _parse_state(state)
    if not conn_id:
        raise HTTPException(400, "Invalid OAuth state signature")
    conn = (await db.execute(select(QboConnection).where(
        QboConnection.id == conn_id))).scalar_one_or_none()
    if not conn or not conn.state_nonce or conn.state_nonce != nonce:
        raise HTTPException(400, "Invalid or expired OAuth state")
    tok = await _exchange_code(code)
    conn.realm_id = realm_id
    conn.access_token = _enc(tok.get("access_token"))
    conn.refresh_token = _enc(tok.get("refresh_token"))
    conn.token_expires_at = datetime.utcnow() + timedelta(seconds=int(tok.get("expires_in", 3600)))
    conn.connected = True
    conn.state_nonce = None
    await db.commit()
    await db.refresh(conn)
    return conn


async def _exchange_code(code: str) -> dict:
    import base64
    import httpx
    basic = base64.b64encode(f"{settings.QBO_CLIENT_ID}:{settings.QBO_CLIENT_SECRET}".encode()).decode()
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(TOKEN_URL, data={
            "grant_type": "authorization_code", "code": code,
            "redirect_uri": settings.QBO_REDIRECT_URI},
            headers={"Authorization": f"Basic {basic}",
                     "Accept": "application/json",
                     "Content-Type": "application/x-www-form-urlencoded"})
    if resp.status_code not in (200, 201):
        from fastapi import HTTPException
        raise HTTPException(502, f"Intuit token exchange failed: {resp.status_code}")
    return resp.json()


async def _refresh(db: AsyncSession, conn: QboConnection) -> None:
    """Refresh the access token using the stored refresh token. Persists the new
    (encrypted) tokens. Intuit rotates the refresh token, so we store both."""
    import base64

    import httpx
    rt = _dec(conn.refresh_token)
    if not rt:
        raise RuntimeError("No refresh token on file")
    basic = base64.b64encode(f"{settings.QBO_CLIENT_ID}:{settings.QBO_CLIENT_SECRET}".encode()).decode()
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(TOKEN_URL, data={"grant_type": "refresh_token", "refresh_token": rt},
                                 headers={"Authorization": f"Basic {basic}",
                                          "Accept": "application/json",
                                          "Content-Type": "application/x-www-form-urlencoded"})
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Intuit token refresh failed: {resp.status_code}")
    tok = resp.json()
    if tok.get("access_token"):
        conn.access_token = _enc(tok["access_token"])
    if tok.get("refresh_token"):
        conn.refresh_token = _enc(tok["refresh_token"])
    conn.token_expires_at = datetime.utcnow() + timedelta(seconds=int(tok.get("expires_in", 3600)))
    await db.commit()


async def _ensure_fresh(db: AsyncSession, conn: QboConnection) -> None:
    """Refresh proactively if the access token is expired/near-expiry (60s skew)."""
    if conn.token_expires_at and conn.token_expires_at <= datetime.utcnow() + timedelta(seconds=60):
        await _refresh(db, conn)


# ── sync ─────────────────────────────────────────────────────────────────────
async def _existing_mapping(db, org_id, entity_type, local_id) -> Optional[IntegrationMapping]:
    return (await db.execute(select(IntegrationMapping).where(
        IntegrationMapping.org_id == org_id, IntegrationMapping.integration == "qbo",
        IntegrationMapping.entity_type == entity_type,
        IntegrationMapping.local_id == local_id))).scalar_one_or_none()


async def _log(db, org_id, entity_type, local_id, status, remote_id=None, error=None):
    db.add(IntegrationSyncLog(org_id=org_id, integration="qbo", entity_type=entity_type,
                              local_id=local_id, direction="push", status=status,
                              remote_id=remote_id, error=error))


async def sync_entity(db: AsyncSession, org_id: str, entity_type: str, local_id: str) -> dict:
    """Push one local record to QBO. Returns the sync result. Never raises on
    provider/network failure — records 'failed' and returns it."""
    conn = await get_connection(db, org_id)
    if not is_configured() or not conn or not conn.connected:
        await _log(db, org_id, entity_type, local_id, "skipped", error="QBO not connected")
        await db.commit()
        return {"status": "skipped", "reason": "QBO not connected"}

    existing = await _existing_mapping(db, org_id, entity_type, local_id)
    payload = await _build_payload(db, org_id, entity_type, local_id)
    if payload is None:
        await _log(db, org_id, entity_type, local_id, "failed", error="Local record not found")
        await db.commit()
        return {"status": "failed", "reason": "Local record not found"}

    try:
        await _ensure_fresh(db, conn)     # refresh expired access token before pushing
        remote_id = await _push(conn, entity_type, payload, existing.remote_id if existing else None)
        if existing:
            existing.remote_id = remote_id
        else:
            db.add(IntegrationMapping(org_id=org_id, integration="qbo", entity_type=entity_type,
                                      local_id=local_id, remote_id=remote_id))
        await _log(db, org_id, entity_type, local_id, "ok", remote_id=remote_id)
        await db.commit()
        return {"status": "ok", "remote_id": remote_id}
    except Exception as exc:  # provider/network failure -> recorded, never raised
        await _log(db, org_id, entity_type, local_id, "failed", error=str(exc)[:300])
        await db.commit()
        return {"status": "failed", "reason": str(exc)[:200]}


async def autosync(db: AsyncSession, org_id: str, items: list[tuple]) -> None:
    """Build 144z — fire-and-forget push of (entity_type, local_id) pairs when an
    org has QBO connected. Silently no-ops when QBO isn't configured/connected, so
    it's safe to call from the invoice/payment flows unconditionally. Pass items in
    dependency order (contact → invoice → payment). Never raises."""
    try:
        conn = await get_connection(db, org_id)
        if not is_configured() or not conn or not conn.connected:
            return
        for entity_type, local_id in items:
            if local_id:
                await sync_entity(db, org_id, entity_type, local_id)
    except Exception:
        pass


async def _remote_id(db, org_id, entity_type, local_id) -> Optional[str]:
    """The QBO id a local record is mapped to, if it's been synced before."""
    if not local_id:
        return None
    m = await _existing_mapping(db, org_id, entity_type, local_id)
    return m.remote_id if m else None


async def _build_payload(db, org_id, entity_type, local_id) -> Optional[dict]:
    from backend.models import OrderLine, Part

    if entity_type == "contact":
        c = (await db.execute(select(Client).where(Client.id == local_id, Client.org_id == org_id))).scalar_one_or_none()
        if not c:
            return None
        return {"DisplayName": c.name, "PrimaryEmailAddr": {"Address": c.email} if c.email else None,
                "PrimaryPhone": {"FreeFormNumber": c.phone} if c.phone else None}

    if entity_type == "item":   # build 144ag: a product → QBO Service item
        p = (await db.execute(select(Part).where(Part.id == local_id, Part.org_id == org_id))).scalar_one_or_none()
        if not p:
            return None
        return {"Name": p.name, "Sku": p.sku or None, "Type": "Service",
                "UnitPrice": float(p.sell_price or 0)}

    if entity_type == "invoice":
        i = (await db.execute(select(Invoice).where(Invoice.id == local_id, Invoice.org_id == org_id))).scalar_one_or_none()
        if not i:
            return None
        payload = {"DocNumber": i.invoice_number, "TotalAmt": i.total}
        # link to the QBO customer if we've synced the client
        cust = await _remote_id(db, org_id, "contact", i.client_id)
        if cust:
            payload["CustomerRef"] = {"value": cust}
        # build lines from the order, referencing mapped QBO items where possible
        lines = []
        tax_total = 0.0
        if i.order_id:
            ols = (await db.execute(select(OrderLine).where(OrderLine.order_id == i.order_id))).scalars().all()
            for ln in ols:
                amt = float(ln.total or 0)
                detail = {"Qty": float(ln.qty or 1), "UnitPrice": float(ln.unit_price or 0)}
                if (ln.line_type or "") == "part" and ln.ref_id:
                    item_ref = await _remote_id(db, org_id, "item", ln.ref_id)
                    if item_ref:
                        detail["ItemRef"] = {"value": item_ref}
                lines.append({"Amount": amt, "Description": ln.description or None,
                              "DetailType": "SalesItemLineDetail", "SalesItemLineDetail": detail})
                tax_total += float(ln.total or 0) * float(ln.tax_rate or 0) / 100.0
        if not lines:   # fall back to a single summary line
            lines = [{"Amount": i.total, "DetailType": "SalesItemLineDetail",
                      "SalesItemLineDetail": {"Qty": 1, "UnitPrice": i.total}}]
        payload["Line"] = lines
        if tax_total > 0:
            payload["TxnTaxDetail"] = {"TotalTax": q2(tax_total)}
        return payload

    if entity_type == "payment":
        p = (await db.execute(select(Payment).where(Payment.id == local_id))).scalar_one_or_none()
        if not p:
            return None
        payload = {"TotalAmt": p.amount, "PaymentRefNum": p.reference or p.id[:8]}
        cust = await _remote_id(db, org_id, "contact", p.client_id)
        if cust:
            payload["CustomerRef"] = {"value": cust}
        return payload
    return None


async def _push(conn: QboConnection, entity_type: str, payload: dict, remote_id: Optional[str]) -> str:
    import httpx
    entity = {"contact": "customer", "invoice": "invoice", "payment": "payment", "item": "item"}[entity_type]
    host = ("https://sandbox-quickbooks.api.intuit.com" if conn.environment == "sandbox"
            else "https://quickbooks.api.intuit.com")
    url = f"{host}/v3/company/{conn.realm_id}/{entity}"
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(url, json=payload, headers={
            "Authorization": f"Bearer {_dec(conn.access_token)}",
            "Accept": "application/json", "Content-Type": "application/json"})
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"QBO {entity} push {resp.status_code}: {resp.text[:160]}")
    body = resp.json()
    obj = body.get(entity.capitalize()) or body.get(entity) or {}
    return str(obj.get("Id") or "unknown")


async def _query(conn: QboConnection, sql: str) -> list[dict]:
    """Run a QBO SQL-like query and return the entity records. Best-effort: []."""
    import httpx
    host = ("https://sandbox-quickbooks.api.intuit.com" if conn.environment == "sandbox"
            else "https://quickbooks.api.intuit.com")
    url = f"{host}/v3/company/{conn.realm_id}/query"
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(url, params={"query": sql}, headers={
            "Authorization": f"Bearer {_dec(conn.access_token)}", "Accept": "application/json"})
    if r.status_code != 200:
        return []
    qr = r.json().get("QueryResponse", {})
    for key in ("Customer", "Item", "Invoice", "Payment"):
        if key in qr:
            return qr[key]
    return []


async def pull(db: AsyncSession, org_id: str, entity_type: str) -> dict:
    """Build 144ag — two-way: import existing QBO customers/items and link them to
    Tekly records by name/email, so future pushes *update* rather than duplicate.
    Never creates local records; only fills the mapping table. Gated + best-effort.
    """
    from backend.models import Client, Part

    if entity_type not in ("contact", "item"):
        raise ValueError("pull supports 'contact' or 'item'")
    conn = await get_connection(db, org_id)
    if not is_configured() or not conn or not conn.connected:
        return {"status": "skipped", "reason": "QBO not connected", "linked": 0}
    try:
        await _ensure_fresh(db, conn)
    except Exception:
        return {"status": "failed", "reason": "token refresh failed", "linked": 0}

    linked = 0
    try:
        if entity_type == "contact":
            remote = await _query(conn, "SELECT Id, DisplayName, PrimaryEmailAddr FROM Customer MAXRESULTS 500")
            locals_ = (await db.execute(select(Client).where(Client.org_id == org_id))).scalars().all()
            by_name = {(c.name or "").strip().lower(): c for c in locals_}
            by_email = {(c.email or "").strip().lower(): c for c in locals_ if c.email}
            for r in remote:
                email = ((r.get("PrimaryEmailAddr") or {}).get("Address") or "").strip().lower()
                match = by_email.get(email) or by_name.get((r.get("DisplayName") or "").strip().lower())
                if match and not await _existing_mapping(db, org_id, "contact", match.id):
                    db.add(IntegrationMapping(org_id=org_id, integration="qbo", entity_type="contact",
                                              local_id=match.id, remote_id=str(r.get("Id"))))
                    linked += 1
        else:  # item
            remote = await _query(conn, "SELECT Id, Name, Sku FROM Item MAXRESULTS 500")
            locals_ = (await db.execute(select(Part).where(Part.org_id == org_id))).scalars().all()
            by_name = {(p.name or "").strip().lower(): p for p in locals_}
            by_sku = {(p.sku or "").strip().lower(): p for p in locals_ if p.sku}
            for r in remote:
                match = by_sku.get((r.get("Sku") or "").strip().lower()) or by_name.get((r.get("Name") or "").strip().lower())
                if match and not await _existing_mapping(db, org_id, "item", match.id):
                    db.add(IntegrationMapping(org_id=org_id, integration="qbo", entity_type="item",
                                              local_id=match.id, remote_id=str(r.get("Id"))))
                    linked += 1
        await db.commit()
    except Exception as exc:
        return {"status": "failed", "reason": str(exc)[:160], "linked": linked}
    return {"status": "ok", "linked": linked, "seen": len(remote)}
