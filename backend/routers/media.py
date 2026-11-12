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
from backend.services.media import _decode_payload, active_backend, load, store

router = APIRouter(prefix="/api/media", tags=["media"])

PUBLIC_MEDIA_ITEMS = ("photo", "signature", "file")


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
    kw = safe_media_response(raw, mime, blob.name)
    # A blob's bytes never change under its id (PERF-02): let the signed-in browser keep it for
    # a while instead of re-downloading every thumbnail. Never a shared cache.
    kw["headers"]["Cache-Control"] = MEDIA_CACHE
    return Response(content=raw, **kw)


MEDIA_CACHE = "private, max-age=3600"


# ── Customer-facing media (PERF-01) ─────────────────────────────────────────────────────────
# The track page and the portal's job view used to carry every photo inline as base64 in a JSON
# polled every 20 s. They now carry these URLs. The link's own token is the key: the job behind
# it must own the item, and the item must be one the customer is shown. Nothing else opens.

async def _feed_media(db: AsyncSession, order, item_id: str) -> Response:
    from backend.models import EventFeedItem
    it = (await db.execute(select(EventFeedItem).where(
        EventFeedItem.id == item_id,
        EventFeedItem.org_id == order.org_id,
        EventFeedItem.entity_type == "order",
        EventFeedItem.entity_id == order.id,
        EventFeedItem.visibility == "client",
        EventFeedItem.item_type.in_(PUBLIC_MEDIA_ITEMS)))).scalar_one_or_none()
    if not it:
        raise HTTPException(404, "Media not found")
    if it.media_blob_id:
        blob = (await db.execute(select(MediaBlob).where(
            MediaBlob.id == it.media_blob_id, MediaBlob.org_id == order.org_id))).scalar_one_or_none()
        if not blob:
            raise HTTPException(404, "Media not found")
        raw, mime = await load(blob)
        mime = it.media_mime or mime
    elif it.media_data:
        raw, sniffed = _decode_payload(it.media_data)
        mime = it.media_mime or sniffed or "application/octet-stream"
    else:
        raise HTTPException(404, "Media not found")
    return _public_media(raw, mime, it.media_name)


async def _rec_media(db: AsyncSession, order, rec_id: str) -> Response:
    from backend.models import Recommendation
    from backend.routers.public import _recommend_enabled
    r = (await db.execute(select(Recommendation).where(
        Recommendation.id == rec_id,
        Recommendation.org_id == order.org_id,
        Recommendation.order_id == order.id,
        Recommendation.status.in_(("sent", "approved", "declined"))))).scalar_one_or_none()
    if not r or not r.media_data or not await _recommend_enabled(db, order.org_id):
        raise HTTPException(404, "Media not found")
    raw, sniffed = _decode_payload(r.media_data)
    return _public_media(raw, sniffed or "application/octet-stream", None)


def _public_media(raw: bytes, mime: str, name) -> Response:
    kw = safe_media_response(raw, mime, name)
    kw["headers"]["Cache-Control"] = MEDIA_CACHE
    kw["headers"]["Referrer-Policy"] = "no-referrer"     # the token is in this URL
    kw["headers"]["X-Robots-Tag"] = "noindex"
    return Response(content=raw, **kw)


async def _track_order(db: AsyncSession, token: str):
    from backend.routers.public import _load
    return await _load(db, token)


async def _portal_order(db: AsyncSession, ptoken: str, order_id: str):
    from backend.models import Order
    from backend.routers.public import _client_by_portal
    client = await _client_by_portal(db, ptoken)
    order = (await db.execute(select(Order).where(
        Order.id == order_id, Order.client_id == client.id,
        Order.org_id == client.org_id))).scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Media not found")
    return order


@router.get("/track/{token}/feed/{item_id}")
async def track_feed_media(token: str, item_id: str, db: AsyncSession = Depends(get_db)):
    return await _feed_media(db, await _track_order(db, token), item_id)


@router.get("/track/{token}/rec/{rec_id}")
async def track_rec_media(token: str, rec_id: str, db: AsyncSession = Depends(get_db)):
    return await _rec_media(db, await _track_order(db, token), rec_id)


@router.get("/portal/{ptoken}/{order_id}/feed/{item_id}")
async def portal_feed_media(ptoken: str, order_id: str, item_id: str, db: AsyncSession = Depends(get_db)):
    return await _feed_media(db, await _portal_order(db, ptoken, order_id), item_id)


@router.get("/portal/{ptoken}/{order_id}/rec/{rec_id}")
async def portal_rec_media(ptoken: str, order_id: str, rec_id: str, db: AsyncSession = Depends(get_db)):
    return await _rec_media(db, await _portal_order(db, ptoken, order_id), rec_id)


# Types a browser may render inline without running script. Everything else — HTML, SVG,
# XML, JS, unknown — is served as an opaque download so an uploaded file can never execute
# as the app (the staff session token lives in this origin's localStorage).
_MAGIC = {
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/gif": (b"GIF87a", b"GIF89a"),
    "application/pdf": (b"%PDF-",),
}
_INLINE_NO_MAGIC = ("image/webp", "image/heic", "image/heif", "audio/", "video/", "text/plain")


def _inline_ok(raw: bytes, mime: str) -> bool:
    if mime in _MAGIC:
        return raw.startswith(_MAGIC[mime])   # declared type must match the bytes
    if mime == "image/webp":
        return raw[:4] == b"RIFF" and raw[8:12] == b"WEBP"
    return mime.startswith(_INLINE_NO_MAGIC)


def safe_media_response(raw: bytes, mime: str, name: str | None) -> dict:
    """Response kwargs (media_type + headers) that keep stored media inert."""
    from urllib.parse import quote
    mime = (mime or "").split(";", 1)[0].strip().lower()
    headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"}
    if mime and _inline_ok(raw, mime):
        if mime != "application/pdf":     # sandboxing a PDF would block the browser's viewer
            headers["Content-Security-Policy"] = "default-src 'none'; img-src 'self' data:; media-src 'self'; sandbox"
        return {"media_type": "text/plain; charset=utf-8" if mime == "text/plain" else mime,
                "headers": headers}
    fname = quote((name or "download").replace("\\", "_").replace("/", "_")[:120])
    headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{fname}"
    headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return {"media_type": "application/octet-stream", "headers": headers}


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
