"""Retired unified inbox (build 104) — webhook forwarding only.

The staff-facing /api/inbox thread endpoints had no frontend and duplicated Chats, so they are
gone (C4). Customer conversations live in ChatThread (/api/chats), which has statuses, spam and
the Chats screen.

Providers may still be configured with the old inbound URLs, so these keep working and forward
to the Chats handlers (same auth, same response shape):

  POST /api/inbox/webhook/{org_id}        →  POST /api/chats/webhook/{org_id}
  GET  /api/inbox/webhook/meta/{org_id}   →  GET  /api/chats/webhook/meta/{org_id}
  POST /api/inbox/webhook/meta/{org_id}   →  POST /api/chats/webhook/meta/{org_id}

InboxThread / InboxMessage stay as the outbound correspondence log (emails sent from invoices,
supplier messages, the contact's Communication tab), which shows Chats messages alongside.
Gmail polling now lands in Chats too.
"""
from typing import Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.routers import chats

router = APIRouter(prefix="/api/inbox", tags=["inbox"])


class LegacyInboundIn(BaseModel):
    channel: str = Field(max_length=20)
    handle: str = Field(max_length=160)
    body: Optional[str] = None
    media_url: Optional[str] = None
    external_thread_id: Optional[str] = None      # ignored: Chats threads by sender
    external_message_id: Optional[str] = Field(None, max_length=160)
    subject: Optional[str] = None


@router.post("/webhook/{org_id}", status_code=201, deprecated=True)
async def inbound_webhook(org_id: str, data: LegacyInboundIn, request: Request,
                          db: AsyncSession = Depends(get_db)):
    """Deprecated: use POST /api/chats/webhook/{org_id}."""
    body = data.body or ""
    if data.media_url:
        body = f"{body}\n{data.media_url}".strip()
    res = await chats.generic_inbound(org_id, chats.InboundIn(
        channel=data.channel, handle=data.handle, body=body,
        external_message_id=data.external_message_id), request, db)
    return {"thread_id": res["thread_id"], "message_id": res["message_id"]}


@router.get("/webhook/meta/{org_id}", include_in_schema=False)
async def meta_verify(org_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Deprecated: use GET /api/chats/webhook/meta/{org_id}."""
    return await chats.meta_verify(org_id, request, db)


@router.post("/webhook/meta/{org_id}", status_code=201, deprecated=True)
async def meta_inbound(org_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Deprecated: use POST /api/chats/webhook/meta/{org_id}."""
    return await chats.meta_inbound(org_id, request, db)
