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
import base64
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


def active_backend() -> str:
    b = (settings.MEDIA_BACKEND or "db").lower()
    if b == "s3" and not settings.MEDIA_S3_BUCKET:
        return "db"          # misconfigured S3 → safe fallback, never lose data
    return b


async def store(db, org_id: str, payload: str, *, name: Optional[str] = None,
                mime: Optional[str] = None) -> MediaBlob:
    raw, sniffed_mime = _decode_payload(payload)
    # Build 144cq: enforce a hard size ceiling on every upload (DoS / abuse guard).
    _cap = int(getattr(settings, "MEDIA_MAX_BYTES", 8 * 1024 * 1024))
    if len(raw) > _cap:
        raise ValueError(f"upload too large (max {_cap // (1024 * 1024)} MB)")
    mime = mime or sniffed_mime
    backend = active_backend()
    blob = MediaBlob(org_id=org_id, backend=backend, mime=mime, name=name,
                     size_bytes=len(raw))

    if backend == "local":
        key = _key_for(org_id, raw, name)
        path = os.path.join(settings.MEDIA_LOCAL_DIR, key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(raw)
        blob.storage_key = key
    elif backend == "s3":
        import boto3  # imported lazily; only when actually offloading to S3
        key = _key_for(org_id, raw, name)
        boto3.client("s3", region_name=settings.MEDIA_S3_REGION).put_object(
            Bucket=settings.MEDIA_S3_BUCKET, Key=key, Body=raw,
            ContentType=mime or "application/octet-stream")
        blob.storage_key = key
    else:  # db (legacy)
        blob.inline_data = payload

    db.add(blob)
    await db.flush()
    return blob


async def load(blob: MediaBlob) -> Tuple[bytes, str]:
    """Return (raw_bytes, mime) for a stored blob, whatever backend it used."""
    mime = blob.mime or "application/octet-stream"
    if blob.backend == "local" and blob.storage_key:
        path = os.path.join(settings.MEDIA_LOCAL_DIR, blob.storage_key)
        with open(path, "rb") as f:
            return f.read(), mime
    if blob.backend == "s3" and blob.storage_key:
        import boto3
        obj = boto3.client("s3", region_name=settings.MEDIA_S3_REGION).get_object(
            Bucket=settings.MEDIA_S3_BUCKET, Key=blob.storage_key)
        return obj["Body"].read(), mime
    # db legacy
    raw, _ = _decode_payload(blob.inline_data or "")
    return raw, mime


def public_ref(blob: MediaBlob) -> str:
    """A stable URL the client can fetch (always via our serving endpoint, which
    streams from whichever backend holds the bytes)."""
    return f"/api/media/{blob.id}"


async def offload_if_enabled(db, org_id, payload, *, name=None, mime=None):
    """Convenience: when a non-db backend is active, store the payload and return
    (blob_id, None) so the caller nulls the inline column. Under db, returns
    (None, payload) to keep it inline. Lets any model adopt offload with one call."""
    if not payload or active_backend() == "db":
        return None, payload
    blob = await store(db, org_id, payload, name=name, mime=mime)
    return blob.id, None
