# B3 — Media Offload (+ A5 status)

## A5 AI — already complete (status correction)
`backend/services/ai.py` + `backend/routers/ai.py` already ship all four A5
features: `draft_product_description` and `recognize_items` call the live model
(with deterministic stubs as fallback), and `transcribe_audio` /
`remove_background` are honest gated seams that return a stable contract until a
speech/image provider is configured. No A5 work was needed this pass.

## B3 — Media offload (built)
Feed photos/signatures/files, part images, and order signatures were stored as
base64 **inline in the DB** — the top scale risk. This adds a storage seam so
media lives outside the primary DB.

### Backends (`MEDIA_BACKEND`)
- `db` (default) — legacy inline; **zero behaviour change**, nothing breaks.
- `local` — bytes to `MEDIA_LOCAL_DIR` (fully tested in-sandbox).
- `s3` — bytes to `MEDIA_S3_BUCKET` via boto3 (gated; falls back to `db` if the
  bucket is unset, so media is never lost through misconfiguration).

### Pieces
- **Model** `MediaBlob(backend, storage_key, inline_data, mime, name,
  size_bytes)` + `EventFeedItem.media_blob_id` pointer.
- **Service** `backend/services/media.py`: `store()` (data-url/base64 → chosen
  backend, content-hash keys), `load()` (bytes back from any backend),
  `public_ref()`.
- **Endpoints** `POST /api/media` (generic upload → id + URL),
  `GET /api/media/{id}` (streams from whichever backend holds the bytes),
  `GET /api/media/status` (backend + offloaded/inline counts),
  `POST /api/media/migrate-feed` (batched, idempotent offload of existing inline
  feed media; no-op under `db`).
- **Feed wiring**: uploads offload automatically when backend≠db; the serializer
  returns `media_url` for offloaded items and keeps `media_data` for legacy ones,
  so old and new clients both work.

### Tests
`backend/tests/test_media_offload.py` (4): db keeps inline; local offloads (bytes
leave the DB) + serves exact original bytes; generic upload + status; migrate-feed
moves inline payloads out. Suite 108. Migration +1 table +2 cols (schema 95).

### Rollout
Ship with `MEDIA_BACKEND=db` (no change), point `local`/`s3` in an env, run
`migrate-feed` per org to drain the DB. Part images + order signatures can adopt
the same `MediaBlob` pattern next (feed was the largest volume).
