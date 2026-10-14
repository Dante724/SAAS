"""144bl — AI posting extractor feeds the preview/confirm gate (never writes directly).

In tests no ANTHROPIC_API_KEY is set, so extraction uses the deterministic text stub. The
contract (rows[] shape) is identical to what the live model returns, and those rows flow into
the existing /postings/preview + /postings/commit path.
"""
import io
import pytest


@pytest.mark.asyncio
async def test_extract_from_pasted_text_returns_rows(client, tenant):
    r = await client.post("/api/inventory/postings/extract",
                          data={"text": "SCR-IP13\tiPhone 13 Screen\t10\t60\nBAT-GS22\tGalaxy S22 Battery\t5\t14"},
                          headers=tenant)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["provider"] == "stub"
    assert len(body["rows"]) == 2
    first = body["rows"][0]
    assert first["code"] == "SCR-IP13" and first["qty"] == 10 and first["purchase_price"] == 60


@pytest.mark.asyncio
async def test_extract_rows_flow_into_preview_and_commit(client, tenant):
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    ext = (await client.post("/api/inventory/postings/extract",
                             data={"text": "WIDGET-1\tExtracted Widget\t4\t9"}, headers=tenant)).json()
    rows = ext["rows"]
    # preview classifies (new, since WIDGET-1 doesn't exist)
    prev = await client.post("/api/inventory/postings/preview", json={"rows": rows}, headers=tenant)
    assert prev.status_code == 200
    assert prev.json()["rows"][0]["match_status"] == "new"
    # commit creates + posts
    commit = await client.post("/api/inventory/postings/commit", json={
        "location_id": loc,
        "rows": [{"new_name": "Extracted Widget", "new_sku": "WIDGET-1", "qty": 4, "purchase_price": 9}],
    }, headers=tenant)
    assert commit.status_code == 201 and commit.json()["created_parts"] == 1


@pytest.mark.asyncio
async def test_extract_requires_input(client, tenant):
    r = await client.post("/api/inventory/postings/extract", data={}, headers=tenant)
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_extract_photo_without_provider_explains(client, tenant):
    img = io.BytesIO(b"\xff\xd8\xff\xe0fakejpeg")
    r = await client.post("/api/inventory/postings/extract",
                          files={"file": ("slip.jpg", img, "image/jpeg")}, headers=tenant)
    assert r.status_code == 200
    body = r.json()
    assert body["rows"] == [] and "note" in body  # gated, honest when no AI key
