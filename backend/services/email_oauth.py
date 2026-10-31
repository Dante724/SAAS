"""A6 — Gmail / Outlook OAuth email adapters (gated seam).

Real send code against the providers' REST APIs (Gmail users.messages.send,
Microsoft Graph sendMail). Inert until an org has a connected EmailConnection
with tokens — exactly like the Square/SumUp/QBO seams. Never live-fired in the
sandbox; unit-tested against a stubbed transport.
"""
import base64
import re
from datetime import datetime, timedelta
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

import httpx

from backend.config import settings
from backend.services.crypto import decrypt as _dec
from backend.services.crypto import encrypt as _enc

GMAIL_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GMAIL_TOKEN = "https://oauth2.googleapis.com/token"
GMAIL_SEND = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
GMAIL_LIST = "https://gmail.googleapis.com/gmail/v1/users/me/messages"
GMAIL_USERINFO = "https://www.googleapis.com/oauth2/v2/userinfo"
# gmail.readonly is added (build 144ae) so the shop's inbox can be polled into
# Tekly. Note: a *restricted* scope — production use needs Google verification
# (CASA). Send-only shops that don't grant it simply can't be polled (handled).
GMAIL_SCOPE = ("https://www.googleapis.com/auth/gmail.send "
               "https://www.googleapis.com/auth/gmail.readonly "
               "https://www.googleapis.com/auth/userinfo.email")

OUTLOOK_AUTH = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
OUTLOOK_TOKEN = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
GRAPH_SEND = "https://graph.microsoft.com/v1.0/me/sendMail"
GRAPH_ME = "https://graph.microsoft.com/v1.0/me"
OUTLOOK_SCOPE = "https://graph.microsoft.com/Mail.Send https://graph.microsoft.com/User.Read offline_access"


def provider_configured(provider: str) -> bool:
    if provider == "gmail":
        return bool(settings.GMAIL_CLIENT_ID and settings.GMAIL_CLIENT_SECRET)
    if provider == "outlook":
        return bool(settings.OUTLOOK_CLIENT_ID and settings.OUTLOOK_CLIENT_SECRET)
    return False


def authorize_url(provider: str, state: str) -> str:
    from urllib.parse import urlencode
    if provider == "gmail":
        return GMAIL_AUTH + "?" + urlencode({
            "client_id": settings.GMAIL_CLIENT_ID, "redirect_uri": settings.EMAIL_OAUTH_REDIRECT,
            "response_type": "code", "scope": GMAIL_SCOPE, "access_type": "offline",
            "prompt": "consent", "state": state})
    return OUTLOOK_AUTH + "?" + urlencode({
        "client_id": settings.OUTLOOK_CLIENT_ID, "redirect_uri": settings.EMAIL_OAUTH_REDIRECT,
        "response_type": "code", "scope": OUTLOOK_SCOPE, "state": state})


async def exchange_code(provider: str, code: str) -> dict:
    data = {"code": code, "redirect_uri": settings.EMAIL_OAUTH_REDIRECT, "grant_type": "authorization_code"}
    if provider == "gmail":
        data.update(client_id=settings.GMAIL_CLIENT_ID, client_secret=settings.GMAIL_CLIENT_SECRET)
        url = GMAIL_TOKEN
    else:
        data.update(client_id=settings.OUTLOOK_CLIENT_ID, client_secret=settings.OUTLOOK_CLIENT_SECRET)
        url = OUTLOOK_TOKEN
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(url, data=data)
        r.raise_for_status()
        return r.json()


async def fetch_email(provider: str, access_token: str) -> Optional[str]:
    """Best-effort: read the connected account's own address from the provider
    (Google userinfo / Graph /me) so the shop's real sender shows in the UI.
    Returns None on any failure — a missing address never blocks connection."""
    if not access_token:
        return None
    try:
        headers = {"Authorization": f"Bearer {access_token}"}
        async with httpx.AsyncClient(timeout=15) as c:
            if provider == "gmail":
                r = await c.get(GMAIL_USERINFO, headers=headers)
                if r.status_code == 200:
                    return r.json().get("email")
            else:
                r = await c.get(GRAPH_ME, headers=headers)
                if r.status_code == 200:
                    j = r.json()
                    return j.get("mail") or j.get("userPrincipalName")
    except Exception:
        return None
    return None


async def _refresh(provider: str, conn) -> None:
    if not conn.refresh_token:
        return
    data = {"refresh_token": _dec(conn.refresh_token), "grant_type": "refresh_token"}
    if provider == "gmail":
        data.update(client_id=settings.GMAIL_CLIENT_ID, client_secret=settings.GMAIL_CLIENT_SECRET)
        url = GMAIL_TOKEN
    else:
        data.update(client_id=settings.OUTLOOK_CLIENT_ID, client_secret=settings.OUTLOOK_CLIENT_SECRET)
        url = OUTLOOK_TOKEN
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(url, data=data)
        r.raise_for_status()
        tok = r.json()
    if tok.get("access_token"):
        conn.access_token = _enc(tok["access_token"])
    if tok.get("refresh_token"):
        conn.refresh_token = _enc(tok["refresh_token"])
    conn.token_expires_at = datetime.utcnow() + timedelta(seconds=int(tok.get("expires_in", 3600)))


async def send_via_connection(conn, *, recipient: str, subject: Optional[str], body: str,
                              attachment: Optional[tuple] = None, html: bool = False) -> dict:
    """Send one message using a connected account. Returns the standard
    {status, error} contract. Guarded: no tokens → 'failed', never raises.

    ``attachment`` is an optional (filename, bytes, mimetype) tuple — used to send
    documents like the invoice PDF (build 144dw). ``html`` sends the body as HTML (only for bodies
    filled from an HTML template, whose values were escaped)."""
    if not conn or not conn.connected or not conn.access_token:
        return {"status": "failed", "error": "Email account not connected"}
    if conn.token_expires_at and conn.token_expires_at <= datetime.utcnow():
        try:
            await _refresh(conn.provider, conn)
        except Exception as exc:
            return {"status": "failed", "error": f"token refresh failed: {exc}"}
    access = _dec(conn.access_token)
    try:
        if conn.provider == "gmail":
            if attachment:
                fname, fdata, fmime = attachment
                msg = MIMEMultipart()
                msg.attach(MIMEText(body, "html" if html else "plain"))
                sub = fmime.split("/", 1)[1] if "/" in fmime else "octet-stream"
                part = MIMEApplication(fdata, _subtype=sub)
                part.add_header("Content-Disposition", "attachment", filename=fname)
                msg.attach(part)
            else:
                msg = MIMEText(body, "html" if html else "plain")
            from backend.services.notifications import one_line
            msg["To"] = recipient
            msg["Subject"] = one_line(subject)
            if conn.email_address:
                msg["From"] = f"{conn.sender_name} <{conn.email_address}>" if conn.sender_name else conn.email_address
            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
            async with httpx.AsyncClient(timeout=20) as c:
                r = await c.post(GMAIL_SEND, json={"raw": raw},
                                 headers={"Authorization": f"Bearer {access}"})
        else:  # outlook / graph
            message = {
                "subject": (subject or "").replace("\r", " ").replace("\n", " "),
                "body": {"contentType": "HTML" if html else "Text", "content": body},
                "toRecipients": [{"emailAddress": {"address": recipient}}]}
            if attachment:
                fname, fdata, fmime = attachment
                message["attachments"] = [{
                    "@odata.type": "#microsoft.graph.fileAttachment",
                    "name": fname, "contentType": fmime,
                    "contentBytes": base64.b64encode(fdata).decode()}]
            payload = {"message": message}
            async with httpx.AsyncClient(timeout=20) as c:
                r = await c.post(GRAPH_SEND, json=payload,
                                 headers={"Authorization": f"Bearer {access}"})
        if r.status_code in (200, 202):
            return {"status": "sent", "error": None}
        return {"status": "failed", "error": f"{conn.provider} {r.status_code}: {r.text[:160]}"}
    except Exception as exc:
        return {"status": "failed", "error": f"{conn.provider}: {exc}"}


async def list_recent(conn, max_results: int = 12) -> list[dict]:
    """Poll a connected Gmail account's inbox for recent messages (build 144ae).
    Returns a normalised list of {message_id, from, from_name, subject, body}.
    Best-effort: no read scope / not connected / any error → []. Gmail only for now.
    """
    if not conn or not conn.connected or not conn.access_token or conn.provider != "gmail":
        return []
    if conn.token_expires_at and conn.token_expires_at <= datetime.utcnow():
        try:
            await _refresh(conn.provider, conn)
        except Exception:
            return []
    out = []
    try:
        access = _dec(conn.access_token)   # inside try: a bad/placeholder token → [] not a raise
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(GMAIL_LIST, params={"q": "in:inbox newer_than:2d", "maxResults": max_results},
                            headers={"Authorization": f"Bearer {access}"})
            if r.status_code != 200:   # 403 = read scope not granted → skip quietly
                return []
            ids = [m["id"] for m in (r.json().get("messages") or [])]
            for mid in ids:
                g = await c.get(f"{GMAIL_LIST}/{mid}",
                                params={"format": "metadata", "metadataHeaders": ["From", "Subject"]},
                                headers={"Authorization": f"Bearer {access}"})
                if g.status_code != 200:
                    continue
                j = g.json()
                hs = {h["name"].lower(): h["value"] for h in j.get("payload", {}).get("headers", [])}
                frm = hs.get("from", "")
                m2 = re.search(r"[\w.\-+]+@[\w.\-]+", frm)
                out.append({"message_id": mid, "from": (m2.group(0) if m2 else frm),
                            "from_name": frm, "subject": hs.get("subject", ""),
                            "body": j.get("snippet", "")})
    except Exception:
        return []
    return out
