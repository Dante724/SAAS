"""A6 — Gmail/Outlook email connector endpoints (gated OAuth seam)."""
import secrets

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.models import EmailConnection, Employee
from backend.services.auth import get_current_employee, require_perm
from backend.services.email_oauth import authorize_url, exchange_code, provider_configured

router = APIRouter(prefix="/api/integrations/email", tags=["integrations"])


@router.get("/status")
async def email_status(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    rows = (await db.execute(select(EmailConnection).where(
        EmailConnection.org_id == current.org_id))).scalars().all()
    return {"providers": {
        "gmail": {"configured": provider_configured("gmail")},
        "outlook": {"configured": provider_configured("outlook")}},
        "accounts": [{"id": r.id, "provider": r.provider, "email": r.email_address,
                      "sender_name": r.sender_name, "connected": r.connected,
                      "is_default": r.is_default} for r in rows]}


class ConnectIn(BaseModel):
    provider: str            # gmail|outlook
    sender_name: str | None = None


@router.post("/connect")
async def email_connect(
    data: ConnectIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    """Begin OAuth: returns the provider authorize URL. Gated — 400 if the
    provider's client credentials aren't configured on the server."""
    if data.provider not in ("gmail", "outlook"):
        raise HTTPException(422, "provider must be gmail|outlook")
    if not provider_configured(data.provider):
        raise HTTPException(400, f"{data.provider} OAuth is not configured on this server")
    nonce = secrets.token_urlsafe(24)
    conn = EmailConnection(org_id=current.org_id, provider=data.provider,
                           sender_name=data.sender_name, state_nonce=nonce, connected=False)
    db.add(conn)
    await db.commit()
    return {"authorize_url": authorize_url(data.provider, f"{conn.id}:{nonce}")}


@router.get("/callback")
async def email_callback(
    state: str = Query(...),
    code: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """OAuth redirect target. Exchanges the code and marks the account connected.
    (Unauthenticated by design — identity is carried in the signed state.)"""
    try:
        conn_id, nonce = state.split(":", 1)
    except ValueError:
        raise HTTPException(400, "Bad state")
    conn = (await db.execute(select(EmailConnection).where(
        EmailConnection.id == conn_id))).scalar_one_or_none()
    if not conn or conn.state_nonce != nonce:
        raise HTTPException(400, "Invalid or expired state")
    from datetime import datetime, timedelta
    from backend.services.crypto import encrypt as _enc
    from backend.services.email_oauth import fetch_email
    tok = await exchange_code(conn.provider, code)
    access = tok.get("access_token")
    conn.access_token = _enc(access)
    if tok.get("refresh_token"):
        conn.refresh_token = _enc(tok["refresh_token"])
    conn.token_expires_at = datetime.utcnow() + timedelta(seconds=int(tok.get("expires_in", 3600)))
    # read the account's own address from the provider (best-effort)
    if access and not conn.email_address:
        conn.email_address = await fetch_email(conn.provider, access)
    conn.connected = True
    conn.state_nonce = None
    # first connected account becomes default
    others = (await db.execute(select(EmailConnection).where(
        EmailConnection.org_id == conn.org_id, EmailConnection.connected == True))).scalars().all()  # noqa: E712
    if not any(o.is_default for o in others if o.id != conn.id):
        conn.is_default = True
    await db.commit()
    return {"ok": True, "provider": conn.provider, "connected": True}


@router.post("/{conn_id}/default")
async def email_set_default(
    conn_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    """Make this connected account the default sender for the org."""
    conn = (await db.execute(select(EmailConnection).where(
        EmailConnection.id == conn_id, EmailConnection.org_id == current.org_id))).scalar_one_or_none()
    if not conn:
        raise HTTPException(404, "Connection not found")
    if not conn.connected:
        raise HTTPException(400, "Connect this account before making it the default")
    rows = (await db.execute(select(EmailConnection).where(
        EmailConnection.org_id == current.org_id))).scalars().all()
    for r in rows:
        r.is_default = (r.id == conn_id)
    await db.commit()
    return {"ok": True, "default_id": conn_id}


@router.delete("/{conn_id}")
async def email_disconnect(
    conn_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    conn = (await db.execute(select(EmailConnection).where(
        EmailConnection.id == conn_id, EmailConnection.org_id == current.org_id))).scalar_one_or_none()
    if not conn:
        raise HTTPException(404, "Connection not found")
    was_default = conn.is_default
    await db.delete(conn)
    await db.flush()
    # if we removed the default, promote another connected account
    if was_default:
        nxt = (await db.execute(select(EmailConnection).where(
            EmailConnection.org_id == current.org_id,
            EmailConnection.connected == True))).scalars().first()  # noqa: E712
        if nxt:
            nxt.is_default = True
    await db.commit()
    return {"ok": True}


class TestSendIn(BaseModel):
    to: str
    subject: str = "RepairOS test email"
    body: str = "This is a test message sent from your connected account via RepairOS."


@router.post("/{conn_id}/test")
async def email_test_send(
    conn_id: str, data: TestSendIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    """Build 144ae: send a test email through a connected Gmail/Outlook account so
    the shop can confirm outbound works before relying on it for notifications."""
    from backend.services.email_oauth import send_via_connection
    conn = (await db.execute(select(EmailConnection).where(
        EmailConnection.id == conn_id, EmailConnection.org_id == current.org_id))).scalar_one_or_none()
    if not conn:
        raise HTTPException(404, "Email account not found")
    res = await send_via_connection(conn, recipient=data.to, subject=data.subject, body=data.body)
    await db.commit()   # persist any token refresh
    return res


@router.post("/poll")
async def email_poll(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    """Build 144ae: pull recent messages from the org's connected Gmail inbox(es)
    into the RepairOS inbox as threads. Idempotent (de-duped by Gmail message id).
    Intended for a scheduled task. No-op unless a Gmail account granted read access."""
    from sqlalchemy import or_ as _or

    from backend.models import Client, InboxMessage, InboxThread
    from backend.services.email_oauth import list_recent
    from datetime import datetime as _dt

    conns = (await db.execute(select(EmailConnection).where(
        EmailConnection.org_id == current.org_id, EmailConnection.provider == "gmail",
        EmailConnection.connected == True))).scalars().all()  # noqa: E712
    ingested = 0
    for conn in conns:
        for m in await list_recent(conn):
            # de-dupe on the Gmail message id
            dup = (await db.execute(select(InboxMessage).where(
                InboxMessage.org_id == current.org_id, InboxMessage.external_id == m["message_id"]))).scalars().first()
            if dup:
                continue
            addr = m["from"]
            thread = (await db.execute(select(InboxThread).where(
                InboxThread.org_id == current.org_id, InboxThread.channel == "email",
                InboxThread.handle == addr).order_by(InboxThread.last_message_at.desc()))).scalars().first()
            if not thread:
                client = (await db.execute(select(Client).where(
                    Client.org_id == current.org_id, _or(Client.email == addr, Client.phone == addr)))).scalars().first()
                thread = InboxThread(org_id=current.org_id, channel="email", handle=addr,
                                     subject=m.get("subject") or "(no subject)",
                                     client_id=client.id if client else None)
                db.add(thread)
                await db.flush()
            db.add(InboxMessage(org_id=current.org_id, thread_id=thread.id, direction="inbound",
                                body=(m.get("subject", "") + "\n\n" + m.get("body", "")).strip(),
                                external_id=m["message_id"]))
            thread.unread = True
            thread.status = "open"
            thread.last_message_at = _dt.utcnow()
            ingested += 1
        await db.commit()   # persist token refresh + threads per connection
    return {"ingested": ingested}
