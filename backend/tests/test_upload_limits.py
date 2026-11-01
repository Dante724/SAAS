"""Request and import size limits (security phase 4, audit L4).

Bodies were read whole into memory with no cap, an .xlsx import inflated whatever zip it was
given, and importers looped over unbounded row lists issuing queries per row.
"""
import io
import zipfile

import pytest

from backend.core.spreadsheet import MAX_IMPORT_ROWS

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest.mark.asyncio
async def test_oversized_body_is_rejected_before_routing(client, tenant, monkeypatch):
    import main
    from backend.core.body_limit import BodySizeLimitMiddleware
    mw = next(m for m in main.app.user_middleware if m.cls is BodySizeLimitMiddleware)
    assert mw.kwargs["max_bytes"] > 0
    big = b'{"name": "' + b"x" * (mw.kwargs["max_bytes"] + 10) + b'"}'
    r = await client.post("/api/clients", content=big,
                          headers={**tenant, "content-type": "application/json"})
    assert r.status_code == 413, r.text[:200]


@pytest.mark.asyncio
async def test_streamed_body_without_length_is_cut_off():
    from backend.core.body_limit import BodySizeLimitMiddleware
    reached = {}

    async def app(scope, receive, send):
        while True:
            m = await receive()
            if not m.get("more_body"):
                break
        reached["yes"] = True

    chunks = [{"type": "http.request", "body": b"x" * 600, "more_body": True} for _ in range(3)]
    chunks.append({"type": "http.request", "body": b"", "more_body": False})
    sent = []

    async def receive():
        return chunks.pop(0)

    async def send(m):
        sent.append(m)

    await BodySizeLimitMiddleware(app, max_bytes=1000)({"type": "http", "headers": []}, receive, send)
    assert "yes" not in reached
    assert sent[0]["status"] == 413


def _bomb() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("xl/worksheets/sheet1.xml", b"0" * (60 * 1024 * 1024))
    return buf.getvalue()


@pytest.mark.asyncio
async def test_xlsx_zip_bomb_is_refused(client, tenant):
    raw = _bomb()
    assert len(raw) < 1024 * 1024        # tiny on the wire
    r = await client.post("/api/catalog/products/import", headers=tenant,
                          files={"file": ("p.xlsx", raw, XLSX)})
    assert r.status_code == 413, r.text


@pytest.mark.asyncio
async def test_not_an_xlsx_is_a_clean_422(client, tenant):
    r = await client.post("/api/catalog/products/import", headers=tenant,
                          files={"file": ("p.xlsx", b"hello", XLSX)})
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_row_lists_are_capped(client, tenant):
    rows = [{"name": "x"}] * (MAX_IMPORT_ROWS + 1)
    r = await client.post("/api/clients/import", json={"rows": rows, "dry_run": True}, headers=tenant)
    assert r.status_code == 422, r.text[:200]
    r = await client.post("/api/clients/import", json={"rows": rows[:5], "dry_run": True}, headers=tenant)
    assert r.status_code == 200, r.text
