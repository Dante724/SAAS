"""B3 — media serving + storage status.

Streams a blob from whichever backend holds its bytes, so the client always uses
one stable URL (`/api/media/{id}`) regardless of db/local/s3.
"""

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.models import Employee, MediaBlob
from backend.services.auth import get_current_employee, require_perm
from backend.services.media import active_backend, load, store

router = APIRouter(prefix="/api/media", tags=["media"])


@router.get("/status")
async def storage_status(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Current backend + how much media is offloaded vs still inline in the DB."""
    total, inline_bytes = (await db.execute(select(
        func.count(MediaBlob.id),
        func.coalesce(func.sum(MediaBlob.size_bytes), 0),
    ).where(MediaBlob.org_id == current.org_id))).one()
    offloaded = (await db.execute(select(func.count(MediaBlob.id)).where(
        MediaBlob.org_id == current.org_id, MediaBlob.backend != "db"))).scalar_one()
    return {"backend": active_backend(), "blobs": total,
            "offloaded_blobs": offloaded, "tracked_bytes": int(inline_bytes)}


@router.post("", status_code=201)
async def upload_media(
    payload: dict,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Generic upload → returns a blob id + stable URL. Body: {data, name?, mime?}."""
    data = payload.get("data")
    if not data:
        raise HTTPException(400, "data (base64 or data URL) is required")
    try:
        blob = await store(db, current.org_id, data,
                           name=payload.get("name"), mime=payload.get("mime"))
    except ValueError as e:   # Build 144cq: oversize upload
        raise HTTPException(413, str(e))
    await db.commit()
    return {"id": blob.id, "url": f"/api/media/{blob.id}", "backend": blob.backend,
            "size_bytes": blob.size_bytes}


@router.get("/{blob_id}")
async def get_media(
    blob_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    blob = (await db.execute(select(MediaBlob).where(
        MediaBlob.id == blob_id, MediaBlob.org_id == current.org_id))).scalar_one_or_none()
    if not blob:
        raise HTTPException(404, "Media not found")
    raw, mime = await load(blob)
    return Response(content=raw, media_type=mime)


@router.post("/migrate-feed")
async def migrate_feed_media(
    limit: int = 500,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    """Offload existing inline feed media into the active backend (local/s3),
    replacing inline payloads with blob pointers. No-op when backend=db.
    Idempotent + batched; returns how many items were moved."""
    from backend.models import EventFeedItem
    if active_backend() == "db":
        return {"migrated": 0, "note": "MEDIA_BACKEND=db — nothing to offload"}
    rows = (await db.execute(select(EventFeedItem).where(
        EventFeedItem.org_id == current.org_id,
        EventFeedItem.media_data.isnot(None),
        EventFeedItem.media_blob_id.is_(None)).limit(limit))).scalars().all()
    moved = 0
    for it in rows:
        blob = await store(db, current.org_id, it.media_data,
                          name=it.media_name, mime=it.media_mime)
        it.media_blob_id = blob.id
        it.media_data = None
        moved += 1
    await db.commit()
    return {"migrated": moved, "backend": active_backend()}
