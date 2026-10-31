"""Customer-portal magic links (/portal/{token}).

Only the SHA-256 of a token is stored and every link expires after LINK_DAYS, so a database
leak or an old forwarded email doesn't open a customer's history for good. Each message that
carries {portal_link} mints its own link (the raw token is never kept to reuse); issuing one
from the client page replaces every earlier link.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

LINK_DAYS = 90


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


async def issue(db: AsyncSession, client, *, replace: bool = False) -> str:
    """A new raw token for this client (flushed, not committed). replace=True revokes the rest."""
    from backend.models import ClientPortalLink
    if replace:
        await revoke_all(db, client.id)
    raw = secrets.token_urlsafe(24)
    db.add(ClientPortalLink(org_id=client.org_id, client_id=client.id, token_hash=_hash(raw),
                            expires_at=datetime.utcnow() + timedelta(days=LINK_DAYS)))
    await db.flush()
    return raw


async def revoke_all(db: AsyncSession, client_id: str) -> None:
    from backend.models import ClientPortalLink
    await db.execute(delete(ClientPortalLink).where(ClientPortalLink.client_id == client_id))


async def client_for(db: AsyncSession, raw: str):
    """The client a live (unexpired) token opens, else None."""
    from backend.models import Client, ClientPortalLink
    if not raw:
        return None
    return (await db.execute(select(Client).join(ClientPortalLink, ClientPortalLink.client_id == Client.id).where(
        ClientPortalLink.token_hash == _hash(raw),
        ClientPortalLink.expires_at > datetime.utcnow()))).scalar_one_or_none()


async def purge_expired(db: AsyncSession) -> int:
    from backend.models import ClientPortalLink
    res = await db.execute(delete(ClientPortalLink).where(ClientPortalLink.expires_at <= datetime.utcnow()))
    return res.rowcount or 0
