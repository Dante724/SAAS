"""
Event feed — polymorphic timeline for work orders and client profiles.
Handles comments, photos, signatures, file attachments, and system events.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core import paging as _paging
from backend.db.database import get_db
from backend.models import Client, Employee, EventFeedItem, Order
from backend.services import audit
from backend.services.auth import get_current_employee

router = APIRouter(prefix="/api/feed", tags=["event-feed"])

VALID_ENTITIES = {"order", "client"}
VALID_ITEMS = {"comment", "photo", "signature", "file"}
PROTECTED_ITEMS = {"system", "status", "signature", "client_message"}
MAX_MEDIA_CHARS = 8_000_000  # ~6 MB base64 ceiling for the demo


class FeedItemIn(BaseModel):
    item_type: str                      # comment | photo | signature | file
    body: Optional[str] = None
    media_data: Optional[str] = None    # data URL / base64
    media_name: Optional[str] = None
    media_mime: Optional[str] = None
    visibility: str = "private"         # private | client


def _serialize(it: EventFeedItem) -> dict:
    return {
        "id": it.id,
        "item_type": it.item_type,
        "body": it.body,
        "media_data": it.media_data,
        "media_url": (f"/api/media/{it.media_blob_id}" if it.media_blob_id else None),
        "media_name": it.media_name,
        "media_mime": it.media_mime,
        "visibility": it.visibility,
        "author_name": it.author_name,
        "created_at": it.created_at.isoformat(),
    }


async def _verify_entity(db: AsyncSession, org_id: str, entity_type: str, entity_id: str,
                         current: Optional[Employee] = None):
    if entity_type == "order":
        row = await db.execute(select(Order).where(Order.id == entity_id, Order.org_id == org_id))
    else:
        row = await db.execute(select(Client).where(Client.id == entity_id, Client.org_id == org_id))
    rec = row.scalar_one_or_none()
    if not rec:
        raise HTTPException(404, f"{entity_type} not found")
    # The path names {entity_id}, so the router-level scope guard never sees it: apply the
    # caller's role scope (own orders/contacts, locations, hidden statuses) here.
    if current is not None:
        from backend.services.scoping import can_view_client, can_view_order
        ok = await (can_view_order(db, current, rec) if entity_type == "order" else can_view_client(db, current, rec))
        if not ok:
            raise HTTPException(403, f"You can only see {entity_type}s within your access")


async def log_system_event(db: AsyncSession, org_id: str, entity_type: str,
                           entity_id: str, message: str):
    """Helper other routers can call to auto-log status changes etc."""
    db.add(EventFeedItem(
        org_id=org_id, entity_type=entity_type, entity_id=entity_id,
        item_type="system", body=message, author_name="System",
    ))


@router.get("/{entity_type}/{entity_id}")
async def list_feed(
    entity_type: str,
    entity_id: str,
    limit: int = _paging.limit_query(200),     # newest first; a busy job's timeline is capped
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    if entity_type not in VALID_ENTITIES:
        raise HTTPException(400, "Invalid entity type")
    await _verify_entity(db, current.org_id, entity_type, entity_id, current)
    result = await db.execute(
        select(EventFeedItem)
        .where(
            EventFeedItem.org_id == current.org_id,
            EventFeedItem.entity_type == entity_type,
            EventFeedItem.entity_id == entity_id,
        )
        .order_by(desc(EventFeedItem.created_at))
        .limit(_paging.clamp(limit))
    )
    items = result.scalars().all()
    # 144ba: opening a work-order feed clears its unread customer-message badge
    if entity_type == "order":
        o = (await db.execute(select(Order).where(Order.id == entity_id, Order.org_id == current.org_id))).scalar_one_or_none()
        if o is not None and (getattr(o, "client_msg_unread", 0) or 0):
            o.client_msg_unread = 0
            await db.commit()
    return {"items": [_serialize(i) for i in items]}


@router.post("/{entity_type}/{entity_id}", status_code=201)
async def create_feed_item(
    entity_type: str,
    entity_id: str,
    data: FeedItemIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    if entity_type not in VALID_ENTITIES:
        raise HTTPException(400, "Invalid entity type")
    if data.item_type not in VALID_ITEMS:
        raise HTTPException(400, "Invalid item type")
    if data.item_type == "comment" and not (data.body or "").strip():
        raise HTTPException(400, "Comment body is required")
    if data.item_type in {"photo", "signature", "file"} and not data.media_data:
        raise HTTPException(400, "Media payload is required")
    if data.media_data and len(data.media_data) > MAX_MEDIA_CHARS:
        raise HTTPException(413, "Attachment too large (max ~6 MB)")

    await _verify_entity(db, current.org_id, entity_type, entity_id, current)

    # B3: offload media to the configured backend (local/s3). When backend=db,
    # store() keeps it inline, so behaviour is identical to before.
    media_blob_id = None
    stored_media_data = data.media_data
    if data.media_data:
        from backend.services.media import active_backend, store
        if active_backend() != "db":
            blob = await store(db, current.org_id, data.media_data,
                               name=data.media_name, mime=data.media_mime)
            media_blob_id = blob.id
            stored_media_data = None      # bytes now live outside the DB

    item = EventFeedItem(
        org_id=current.org_id,
        entity_type=entity_type,
        entity_id=entity_id,
        item_type=data.item_type,
        body=data.body,
        media_data=stored_media_data,
        media_blob_id=media_blob_id,
        media_name=data.media_name,
        media_mime=data.media_mime,
        visibility="client" if data.visibility == "client" else "private",
        author_id=current.id,
        author_name=current.name,
    )
    db.add(item)
    if entity_type == "order" and data.item_type == "photo":
        from backend.services import pilot
        pilot.record_photos(db, current, entity_id, 1, "feed")
    await db.commit()
    await db.refresh(item)
    return _serialize(item)


@router.delete("/item/{item_id}")
async def delete_feed_item(
    item_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    result = await db.execute(
        select(EventFeedItem).where(
            EventFeedItem.id == item_id,
            EventFeedItem.org_id == current.org_id,
        )
    )
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(404, "Item not found")
    # Evidence stays: system events, status history, customer signatures and anything the
    # customer wrote. Staff may remove their own posts; removing someone else's needs
    # delete rights on orders.
    if item.item_type in PROTECTED_ITEMS or item.author_id is None:
        raise HTTPException(403, "This entry is part of the record and can't be deleted")
    if item.author_id != current.id:
        from backend.services.auth import has_perm
        if not await has_perm(db, current, "can_delete_orders"):
            raise HTTPException(403, "You can only delete your own posts")
    await audit.record(db, org_id=current.org_id, actor_id=current.id, action="feed.delete",
                       entity_type=item.entity_type, entity_id=item.entity_id,
                       diff={"item_id": item.id, "item_type": item.item_type, "author_id": item.author_id})
    await db.delete(item)
    await db.commit()
    return {"ok": True}
