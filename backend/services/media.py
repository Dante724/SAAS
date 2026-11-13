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
import os
import re
from typing import Optional, Tuple

from backend.config import settings
from backend.models import MediaBlob

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
    return int(getattr(settings, "MEDIA_MAX_BYTES", 8 * 1024 * 1024))


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


def too_large_message() -> str:
    return f"Attachment too large (max {max_bytes() // (1024 * 1024)} MB)"


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
    raw, sniffed_mime = await decode(payload)
    # Build 144cq: enforce a hard size ceiling on every upload (DoS / abuse guard).
    _cap = max_bytes()
    if len(raw) > _cap:
        raise ValueError(f"upload too large (max {_cap // (1024 * 1024)} MB)")
    mime = mime or sniffed_mime
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
    if not payload or active_backend() == "db":
        return None, payload
    blob = await store(db, org_id, payload, name=name, mime=mime)
    return blob.id, None
