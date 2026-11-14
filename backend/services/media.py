"""B3 — media offload service.

A single seam that stores/serves binary media through a pluggable backend so it
no longer bloats the primary DB:

  * db    — legacy: base64/data-url kept inline in MediaBlob.inline_data.
  * local — bytes written to MEDIA_LOCAL_DIR (fully testable in-sandbox).
  * s3    — bytes to an S3 bucket via boto3 (gated behind creds; never fires
            without a bucket configured).

`store()` returns a MediaBlob row; `load()` returns (bytes|data_url, mime).
Existing inline payloads keep working, so migration is incremental.
"""
import asyncio
import base64
import functools
import hashlib
import io
import logging
import os
import re
from typing import Optional, Tuple

from backend.config import settings
from backend.models import MediaBlob

log = logging.getLogger("tekly.media")


class MediaTooLarge(ValueError):
    """A file over MEDIA_MAX_BYTES. main.py answers it with 413 and too_large_message()."""


class MediaStoreUnavailable(RuntimeError):
    """A deployed server with no object storage: the bytes would land in the database (PERF-22).
    main.py answers it with 503. Not a ValueError, so no route's size handler can swallow it."""


STORE_UNAVAILABLE_MESSAGE = ("File storage isn't set up on this server yet, so the file wasn't saved. "
                             "Your other changes are fine. Please let Tekly support know.")

_DATA_URL = re.compile(r"^data:([^;,]+)?(;base64)?,(.*)$", re.S)


def _decode_payload(payload: str) -> Tuple[bytes, Optional[str]]:
    """Accept a data URL or bare base64; return (raw_bytes, mime|None)."""
    m = _DATA_URL.match(payload or "")
    if m:
        mime = m.group(1) or None
        raw = base64.b64decode(m.group(3)) if m.group(2) else m.group(3).encode()
        return raw, mime
    try:
        return base64.b64decode(payload), None
    except Exception:
        return (payload or "").encode(), None


def _key_for(org_id: str, raw: bytes, name: Optional[str]) -> str:
    h = hashlib.sha256(raw).hexdigest()[:32]
    ext = ""
    if name and "." in name:
        ext = "." + name.rsplit(".", 1)[-1][:8]
    return f"{settings.MEDIA_S3_PREFIX}{org_id}/{h}{ext}"


def max_bytes() -> int:
    """The one size ceiling for any attachment, whatever the backend (PERF-22)."""
    return int(settings.MEDIA_MAX_BYTES)


def payload_bytes(payload: Optional[str]) -> int:
    """Decoded size of a data URL / base64 payload, worked out without decoding it."""
    if not payload:
        return 0
    m = _DATA_URL.match(payload[:256]) if payload.startswith("data:") else None
    body = payload.split(",", 1)[1] if m else payload
    if m and not m.group(2):
        return len(body.encode())
    body = body.strip()
    pad = len(body) - len(body.rstrip("="))
    return max(0, (len(body) * 3) // 4 - pad)


def too_large(payload: Optional[str]) -> bool:
    return payload_bytes(payload) > max_bytes()


def _limit_text() -> str:
    n = max_bytes()
    return f"{n / (1024 * 1024):g} MB" if n >= 1024 * 1024 else f"{n // 1024} KB"


def too_large_message() -> str:
    """The one wording for an over-limit file, server and client alike (frontend lib/media.ts)."""
    return f"That file is over the {_limit_text()} limit. Try a smaller file."


def check_size(payload: Optional[str]) -> None:
    """Raise MediaTooLarge for a payload over the limit: for the few fields still kept inline
    (asset photo, shop logo, drawn signature), which don't go through store()."""
    if payload and too_large(payload):
        raise MediaTooLarge(too_large_message())


# ── where the bytes may live (PERF-22, item 5) ──────────────────────────────────────────────
def _deployed() -> bool:
    return settings.is_deployed


def storage_problem() -> Optional[str]:
    """Why this server must not take new uploads, or None. Production and staging never keep
    attachment bytes in the database: without object storage (or a persistent local volume) the
    upload is refused instead of quietly falling back to the database. Dev and tests may use db."""
    if not _deployed():
        return None
    backend = (settings.MEDIA_BACKEND or "db").lower()
    if backend == "local" or (backend == "s3" and settings.MEDIA_S3_BUCKET):
        return None
    if backend == "s3":
        return ("MEDIA_BACKEND=s3 but MEDIA_S3_BUCKET is not set: new uploads are refused "
                "(never kept in the database). Set the bucket and its keys.")
    return (f"MEDIA_BACKEND={backend}: this server has no object storage, so new uploads are refused "
            "(never kept in the database). Set MEDIA_BACKEND=s3 with MEDIA_S3_BUCKET and its keys.")


def warn_if_unconfigured() -> None:
    """Startup: say loudly when uploads will be refused (logged at ERROR, so alerting sees it)."""
    problem = storage_problem()
    if problem:
        log.error("media storage not configured: %s", problem)


def _require_store() -> None:
    if storage_problem():
        raise MediaStoreUnavailable(STORE_UNAVAILABLE_MESSAGE)


# ── photos shrink on the server too (PERF-22, item 3) ───────────────────────────────────────
# The apps shrink photos before upload; this is the safety net for anything that didn't (an old
# app, the API, a browser that couldn't decode it). Only JPEG/PNG/WebP stills are touched; a GIF,
# SVG, PDF or anything we can't decode is stored exactly as sent.
_SHRINKABLE = {"image/jpeg", "image/jpg", "image/pjpeg", "image/png", "image/webp"}
_MAX_PIXELS = 50_000_000      # never decode a "photo" bigger than this (decompression bombs)
_EXT = {"image/jpeg": "jpg", "image/webp": "webp"}


def shrink_image(raw: bytes, mime: Optional[str]) -> Tuple[bytes, Optional[str]]:
    """(bytes, mime) of the photo to keep: at most MEDIA_IMAGE_MAX_EDGE on the long side, turned
    upright, with no EXIF (camera, GPS). A photo already small and clean is kept byte for byte."""
    edge = int(settings.MEDIA_IMAGE_MAX_EDGE or 0)
    if edge <= 0 or (mime or "").lower() not in _SHRINKABLE:
        return raw, mime
    try:
        from PIL import Image, ImageOps
        with Image.open(io.BytesIO(raw)) as img:
            if img.format not in ("JPEG", "PNG", "WEBP") or getattr(img, "is_animated", False):
                return raw, mime
            w, h = img.size
            if not w or not h or w * h > _MAX_PIXELS:
                return raw, mime
            exif = img.getexif()
            if max(w, h) <= edge and not exif:
                return raw, mime
            out_img = ImageOps.exif_transpose(img)
            out_img.thumbnail((edge, edge), Image.LANCZOS)
            alpha = out_img.mode in ("RGBA", "LA", "PA") or (
                out_img.mode == "P" and "transparency" in out_img.info)
            q = int(settings.MEDIA_IMAGE_QUALITY or 80)
            buf = io.BytesIO()
            if alpha:   # keep transparency (a logo, a cut-out): WebP, not JPEG
                out_img.save(buf, "WEBP", quality=q)
                return buf.getvalue(), "image/webp"
            out_img.convert("RGB").save(buf, "JPEG", quality=q, optimize=True)
            return buf.getvalue(), "image/jpeg"
    except Exception:  # noqa: BLE001 — the safety net never loses a file: keep it as sent
        return raw, mime


def _renamed(name: Optional[str], old_mime: Optional[str], new_mime: Optional[str]) -> Optional[str]:
    if not name or new_mime == old_mime or new_mime not in _EXT:
        return name
    base = name.rsplit(".", 1)[0] if "." in name else name
    return f"{base}.{_EXT[new_mime]}"


async def inline_photo(value: Optional[str]) -> Optional[str]:
    """A photo for a field still kept inline (asset photo): the size limit, then the shrink."""
    check_size(value)
    return await shrink_data_url(value)


async def shrink_data_url(value: Optional[str]) -> Optional[str]:
    """shrink_image for a field still kept inline as a data URL (the asset photo)."""
    if not value or not value.startswith("data:image/"):
        return value
    raw, mime = await decode(value)
    new, new_mime = await asyncio.to_thread(shrink_image, raw, mime)
    if new is raw:
        return value
    return f"data:{new_mime};base64," + base64.b64encode(new).decode()


@functools.lru_cache(maxsize=4)
def _s3_client(region: str):
    """One boto3 client per region, reused (PERF-02: a new client per call re-resolved
    credentials and endpoints every time). boto3 clients are thread-safe."""
    import boto3  # imported lazily; only when actually offloading to S3
    return boto3.client("s3", region_name=region or None)


def _s3():
    return _s3_client(settings.MEDIA_S3_REGION or "")


async def _s3_call(method: str, /, **kw):
    """boto3 is blocking: run it on a worker thread so the event loop keeps serving every
    other shop while S3 answers (PERF-02). The client is looked up (and on first use built, which
    resolves credentials, possibly over the network) on that thread too (N-7)."""
    return await asyncio.to_thread(lambda: getattr(_s3(), method)(**kw))


async def decode(payload: str) -> Tuple[bytes, Optional[str]]:
    """_decode_payload on a worker thread: base64 of up to MEDIA_MAX_BYTES is too slow for the loop
    (N-7)."""
    return await asyncio.to_thread(_decode_payload, payload)


def _write_file(path: str, raw: bytes) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(raw)


def _remove_file(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def active_backend() -> str:
    b = (settings.MEDIA_BACKEND or "db").lower()
    if b == "s3" and not settings.MEDIA_S3_BUCKET:
        return "db"          # misconfigured S3 → safe fallback, never lose data
    return b


async def store(db, org_id: str, payload: str, *, name: Optional[str] = None,
                mime: Optional[str] = None) -> MediaBlob:
    _require_store()
    # Cheap first: the size from the base64 length, before decoding anything (DoS guard).
    if too_large(payload):
        raise MediaTooLarge(too_large_message())
    raw, sniffed_mime = await decode(payload)
    if len(raw) > max_bytes():
        raise MediaTooLarge(too_large_message())
    mime = mime or sniffed_mime
    new, new_mime = await asyncio.to_thread(shrink_image, raw, mime)
    if new is not raw:
        name = _renamed(name, mime, new_mime)
        raw, mime = new, new_mime
        payload = f"data:{mime};base64," + base64.b64encode(raw).decode()
    backend = active_backend()
    blob = MediaBlob(org_id=org_id, backend=backend, mime=mime, name=name,
                     size_bytes=len(raw))

    if backend == "local":
        key = _key_for(org_id, raw, name)
        await asyncio.to_thread(_write_file, os.path.join(settings.MEDIA_LOCAL_DIR, key), raw)
        blob.storage_key = key
    elif backend == "s3":
        key = _key_for(org_id, raw, name)
        await _s3_call("put_object", Bucket=settings.MEDIA_S3_BUCKET, Key=key, Body=raw,
                       ContentType=mime or "application/octet-stream")
        blob.storage_key = key
    else:  # db (legacy)
        blob.inline_data = payload

    db.add(blob)
    await db.flush()
    return blob


def _read_file(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


async def load(blob: MediaBlob) -> Tuple[bytes, str]:
    """Return (raw_bytes, mime) for a stored blob, whatever backend it used."""
    mime = blob.mime or "application/octet-stream"
    if blob.backend == "local" and blob.storage_key:
        path = os.path.join(settings.MEDIA_LOCAL_DIR, blob.storage_key)
        return await asyncio.to_thread(_read_file, path), mime
    if blob.backend == "s3" and blob.storage_key:
        def _get(key: str) -> bytes:
            return _s3().get_object(Bucket=settings.MEDIA_S3_BUCKET, Key=key)["Body"].read()
        return await asyncio.to_thread(_get, blob.storage_key), mime
    # db legacy
    raw, _ = await decode(blob.inline_data or "")
    return raw, mime


async def erase(db, blobs: list[MediaBlob]) -> int:
    """Destroy the bytes behind these blobs (GDPR erasure) and empty their rows. Files are
    content-addressed per shop, so an object another surviving blob still points at is kept.
    Returns how many blobs were emptied."""
    from sqlalchemy import select
    ids = {b.id for b in blobs}
    for b in blobs:
        if b.storage_key:
            shared = (await db.execute(select(MediaBlob.id).where(
                MediaBlob.storage_key == b.storage_key, MediaBlob.id.notin_(ids)).limit(1))).first()
            if not shared:
                if b.backend == "local":
                    await asyncio.to_thread(_remove_file, os.path.join(settings.MEDIA_LOCAL_DIR, b.storage_key))
                elif b.backend == "s3":
                    await _s3_call("delete_object", Bucket=settings.MEDIA_S3_BUCKET, Key=b.storage_key)
        b.inline_data, b.storage_key, b.name, b.size_bytes = None, None, None, 0
    return len(blobs)


def public_ref(blob: MediaBlob) -> str:
    """A stable URL the client can fetch (always via our serving endpoint, which
    streams from whichever backend holds the bytes)."""
    return f"/api/media/{blob.id}"


# ── references in plain text columns (PERF-06) ─────────────────────────────────────────────
# A Text column that used to hold a data URL (e.g. recommendations.media_data) can instead hold
# "media:<blob id>": the bytes go through store() like every other upload, and no schema change is
# needed. Readers call load_value(), which takes either form, so old inline rows keep working.
REF_PREFIX = "media:"
_REF = re.compile(r"^media:([0-9A-Za-z-]{1,64})$")


def ref_id(value: Optional[str]) -> Optional[str]:
    """The blob id behind a "media:<id>" value, else None (inline data or empty)."""
    m = _REF.match(value or "")
    return m.group(1) if m else None


def as_ref(blob: MediaBlob) -> str:
    return f"{REF_PREFIX}{blob.id}"


async def store_ref(db, org_id: str, payload: str, *, name: Optional[str] = None,
                    mime: Optional[str] = None) -> str:
    """store() the payload and return the "media:<id>" value to keep in the text column."""
    return as_ref(await store(db, org_id, payload, name=name, mime=mime))


async def _blob(db, org_id: str, blob_id: str) -> Optional[MediaBlob]:
    from sqlalchemy import select
    return (await db.execute(select(MediaBlob).where(
        MediaBlob.id == blob_id, MediaBlob.org_id == org_id))).scalar_one_or_none()


async def load_value(db, org_id: str, value: Optional[str]) -> Optional[Tuple[bytes, Optional[str]]]:
    """(bytes, mime|None) for a column value that is either a "media:<id>" reference (the blob
    must be this shop's) or a legacy inline data URL / base64. None when there is nothing."""
    if not value:
        return None
    bid = ref_id(value)
    if bid:
        blob = await _blob(db, org_id, bid)
        if blob is None or (not blob.inline_data and not blob.storage_key):
            return None
        raw, mime = await load(blob)
        return raw, blob.mime or mime
    return await decode(value)


async def erase_refs(db, org_id: str, values) -> int:
    """Erase the blobs behind any "media:<id>" values (inline values have nothing to erase)."""
    from sqlalchemy import select
    ids = {b for b in (ref_id(v) for v in values) if b}
    if not ids:
        return 0
    blobs = (await db.execute(select(MediaBlob).where(
        MediaBlob.org_id == org_id, MediaBlob.id.in_(ids)))).scalars().all()
    return await erase(db, list(blobs))


async def relocate(blob: MediaBlob) -> bool:
    """Move a blob kept inline (backend "db") to the active backend, keeping its id so every
    pointer to it stays valid. False when there is nothing to move."""
    backend = active_backend()
    if backend == "db" or blob.backend != "db" or not blob.inline_data:
        return False
    raw, sniffed = await decode(blob.inline_data)
    key = _key_for(blob.org_id, raw, blob.name)
    if backend == "local":
        await asyncio.to_thread(_write_file, os.path.join(settings.MEDIA_LOCAL_DIR, key), raw)
    else:
        await _s3_call("put_object", Bucket=settings.MEDIA_S3_BUCKET, Key=key, Body=raw,
                       ContentType=blob.mime or sniffed or "application/octet-stream")
    blob.backend, blob.storage_key, blob.inline_data = backend, key, None
    blob.mime = blob.mime or sniffed
    blob.size_bytes = len(raw)
    return True


# ── backfill (PERF-06) ──────────────────────────────────────────────────────────────────────
async def _keyset(db, stmt, id_col, after: str, batch: int) -> list:
    return list((await db.execute(stmt.where(id_col > after).order_by(id_col).limit(batch)
                                  .execution_options(skip_tenant_filter=True))).scalars().all())


async def backfill(db, *, batch: int = 50, org_id: Optional[str] = None, dry_run: bool = False,
                   on_batch=None) -> dict:
    """Move binary data still kept inline into the media store, a batch at a time, committing
    after each batch. Safe to stop and run again: rows already moved are skipped.

      * event_feed_items.media_data   -> a blob, pointed at by media_blob_id
      * recommendations.media_data    -> a blob, the column keeps "media:<id>"
      * media_blobs kept inline (db)  -> the active backend, when it is local or s3

    Only one payload is held in memory at a time (ids are paged, each row is read on its own).
    `org_id` limits it to one shop. Returns how many of each were moved (or would be, dry_run)."""
    from sqlalchemy import select

    from backend.models import EventFeedItem, Recommendation

    def _scope(stmt, col):
        return stmt.where(col == org_id) if org_id else stmt

    async def _done():
        if dry_run:
            await db.rollback()
        else:
            await db.commit()
        if on_batch:
            on_batch()

    out = {"feed": 0, "recommendations": 0, "blobs": 0}

    # feed attachments
    after = ""
    q = _scope(select(EventFeedItem.id).where(EventFeedItem.media_data.is_not(None),
                                              EventFeedItem.media_data != "",
                                              EventFeedItem.media_blob_id.is_(None)),
               EventFeedItem.org_id)
    while ids := await _keyset(db, q, EventFeedItem.id, after, batch):
        for iid in ids:
            it = (await db.execute(select(EventFeedItem).where(EventFeedItem.id == iid)
                                   .execution_options(skip_tenant_filter=True))).scalar_one()
            if not dry_run:
                blob = await store(db, it.org_id, it.media_data, name=it.media_name, mime=it.media_mime)
                it.media_blob_id, it.media_data = blob.id, None
            out["feed"] += 1
        after = ids[-1]
        await _done()
        db.expunge_all()

    # recommendation photos
    after = ""
    q = _scope(select(Recommendation.id).where(Recommendation.media_data.is_not(None),
                                               Recommendation.media_data != "",
                                               ~Recommendation.media_data.startswith(REF_PREFIX)),
               Recommendation.org_id)
    while ids := await _keyset(db, q, Recommendation.id, after, batch):
        for rid in ids:
            r = (await db.execute(select(Recommendation).where(Recommendation.id == rid)
                                  .execution_options(skip_tenant_filter=True))).scalar_one()
            if not dry_run:
                r.media_data = await store_ref(db, r.org_id, r.media_data)
            out["recommendations"] += 1
        after = ids[-1]
        await _done()
        db.expunge_all()

    # blobs kept inline while MEDIA_BACKEND was db
    if active_backend() != "db":
        after = ""
        q = _scope(select(MediaBlob.id).where(MediaBlob.backend == "db", MediaBlob.inline_data.is_not(None)),
                   MediaBlob.org_id)
        while ids := await _keyset(db, q, MediaBlob.id, after, batch):
            for bid in ids:
                blob = (await db.execute(select(MediaBlob).where(MediaBlob.id == bid)
                                         .execution_options(skip_tenant_filter=True))).scalar_one()
                if dry_run or await relocate(blob):
                    out["blobs"] += 1
            after = ids[-1]
            await _done()
            db.expunge_all()
    return out


async def offload_if_enabled(db, org_id, payload, *, name=None, mime=None):
    """Convenience: when a non-db backend is active, store the payload and return
    (blob_id, None) so the caller nulls the inline column. Under db, returns
    (None, payload) to keep it inline. Lets any model adopt offload with one call."""
    if not payload:
        return None, payload
    _require_store()
    if active_backend() == "db":
        return None, payload
    blob = await store(db, org_id, payload, name=name, mime=mime)
    return blob.id, None
