"""Build 144af — unified messaging for the inbox: WhatsApp, Messenger, Instagram
(plus SMS/email handled elsewhere).

Two directions:
  * send(channel, to, body)  — outbound replies routed to the right provider,
    gated on config. No creds → 'simulated' (logged), never raises.
  * parse_meta_webhook(payload) — normalise a Meta webhook body (Messenger /
    Instagram / WhatsApp Cloud) into the inbox's message shape.

Everything is a gated seam: inert until the org configures the provider, exactly
like the Stripe / Gmail / QBO connectors.
"""
from typing import Optional

import httpx

from backend.config import settings

GRAPH = "https://graph.facebook.com/v19.0"


async def send(channel: str, to: str, body: str, creds: Optional[dict] = None) -> dict:
    """Outbound reply. `creds` are THIS shop's own Meta/Twilio keys (144jn); when
    omitted, falls back to the global env (operator default).
    Returns {status: sent|simulated|failed|skipped, error}."""
    ch = (channel or "").lower()
    c = creds or {}
    if not to:
        return {"status": "skipped", "error": "No recipient handle on file"}
    try:
        if ch == "whatsapp":
            return await _whatsapp_send(to, body, c)
        if ch == "sms":
            return await _twilio_send(to, body, whatsapp=False, creds=c)
        if ch in ("facebook", "instagram"):
            return await _meta_send(to, body, c)
        return {"status": "simulated", "error": None}   # email handled by notifications; call = manual
    except Exception as exc:   # network best-effort — never break the reply
        return {"status": "failed", "error": f"{ch}: {exc}"}


async def _whatsapp_send(to: str, body: str, creds: dict) -> dict:
    # Prefer WhatsApp Cloud API; fall back to Twilio WhatsApp; else simulate.
    wa_token = creds.get("whatsapp_token") or settings.WHATSAPP_TOKEN
    wa_phone = creds.get("whatsapp_phone_id") or settings.WHATSAPP_PHONE_ID
    if wa_token and wa_phone:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                f"{GRAPH}/{wa_phone}/messages",
                headers={"Authorization": f"Bearer {wa_token}"},
                json={"messaging_product": "whatsapp", "to": to.replace("whatsapp:", ""),
                      "type": "text", "text": {"body": body}})
        return {"status": "sent", "error": None} if r.status_code < 300 else {"status": "failed", "error": r.text[:160]}
    if (creds.get("account_sid") or settings.TWILIO_ACCOUNT_SID) and (creds.get("auth_token") or settings.TWILIO_AUTH_TOKEN):
        return await _twilio_send(to, body, whatsapp=True, creds=creds)
    return {"status": "simulated", "error": None}


async def _twilio_send(to: str, body: str, *, whatsapp: bool, creds: dict) -> dict:
    sid = creds.get("account_sid") or settings.TWILIO_ACCOUNT_SID
    token = creds.get("auth_token") or settings.TWILIO_AUTH_TOKEN
    if not (sid and token):
        return {"status": "simulated", "error": None}
    frm = (creds.get("whatsapp_from") or settings.TWILIO_WHATSAPP_FROM) if whatsapp else (creds.get("from") or settings.TWILIO_FROM)
    to_addr = f"whatsapp:{to}" if whatsapp and not to.startswith("whatsapp:") else to
    url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(url, data={"To": to_addr, "From": frm, "Body": body},
                         auth=(sid, token))
    return {"status": "sent", "error": None} if r.status_code < 300 else {"status": "failed", "error": r.text[:160]}


async def _meta_send(to: str, body: str, creds: dict) -> dict:
    token = creds.get("ig_token") or creds.get("page_token") or settings.META_IG_TOKEN or settings.META_PAGE_TOKEN
    if not token:
        return {"status": "simulated", "error": None}
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(f"{GRAPH}/me/messages", params={"access_token": token},
                         json={"recipient": {"id": to}, "message": {"text": body}})
    return {"status": "sent", "error": None} if r.status_code < 300 else {"status": "failed", "error": r.text[:160]}


async def creds_for(db, org_id: str) -> dict:
    """Merged effective Meta + Twilio creds for a shop (144jn)."""
    from backend.services import org_creds
    meta = await org_creds.effective(db, org_id, "meta")
    tw = await org_creds.effective(db, org_id, "twilio")
    return {**meta, **tw}


def verify_subscription(mode: Optional[str], token: Optional[str], challenge: Optional[str]):
    """Meta webhook subscription handshake (GET). Returns the challenge string to
    echo back when the verify token matches, else None."""
    if mode == "subscribe" and token and settings.META_VERIFY_TOKEN and token == settings.META_VERIFY_TOKEN:
        return challenge
    return None


def parse_meta_webhook(payload: dict) -> list[dict]:
    """Normalise a Meta webhook body into inbox messages. Handles Messenger &
    Instagram (`messaging` entries) and WhatsApp Cloud (`changes[].value.messages`).
    Returns [{channel, handle, external_thread_id, external_message_id, subject, body}]."""
    out = []
    for entry in (payload.get("entry") or []):
        # Messenger / Instagram
        for m in (entry.get("messaging") or []):
            sender = (m.get("sender") or {}).get("id")
            text = (m.get("message") or {}).get("text")
            mid = (m.get("message") or {}).get("mid")
            if sender and text:
                channel = "instagram" if payload.get("object") == "instagram" else "facebook"
                out.append({"channel": channel, "handle": sender, "external_thread_id": sender,
                            "external_message_id": mid, "subject": None, "body": text})
        # WhatsApp Cloud
        for ch in (entry.get("changes") or []):
            value = ch.get("value") or {}
            for msg in (value.get("messages") or []):
                frm = msg.get("from")
                text = (msg.get("text") or {}).get("body")
                if frm and text:
                    out.append({"channel": "whatsapp", "handle": frm, "external_thread_id": frm,
                                "external_message_id": msg.get("id"), "subject": None, "body": text})
    return out
