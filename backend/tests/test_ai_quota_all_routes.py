"""Beta blocker — every route that can call the AI model shares the /api/ai budget
(20/min per user, 1000/day per shop) and caps its input size."""
import base64

import pytest


async def _contact(client, tenant):
    return (await client.post("/api/clients", json={"name": "Quota Q"}, headers=tenant)).json()["id"]


async def _burst(client, tenant, call):
    for i in range(20):
        r = await call()
        assert r.status_code == 200, (i, r.text)
    r = await call()
    assert r.status_code == 429
    assert "AI" in r.json()["detail"] and "minute" in r.json()["detail"]
    assert r.headers.get("Retry-After")


async def test_client_suggest_is_rate_limited(client, tenant):
    cid = await _contact(client, tenant)
    await _burst(client, tenant,
                 lambda: client.post(f"/api/clients/{cid}/communication/suggest", headers=tenant))


async def test_posting_extract_is_rate_limited(client, tenant):
    await _burst(client, tenant, lambda: client.post(
        "/api/inventory/postings/extract", data={"text": "2 x iPhone 12 screen @ 40"}, headers=tenant))


async def test_api_ai_routes_share_the_same_budget(client, tenant):
    """Calls on /api/ai and the contact suggest route draw on one per-user counter."""
    cid = await _contact(client, tenant)
    for _ in range(10):
        assert (await client.post("/api/ai/recognize-items", json={"text": "1 x screen"},
                                  headers=tenant)).status_code == 200
    for _ in range(10):
        assert (await client.post(f"/api/clients/{cid}/communication/suggest",
                                  headers=tenant)).status_code == 200
    r = await client.post("/api/ai/recognize-items", json={"text": "1 x screen"}, headers=tenant)
    assert r.status_code == 429


async def test_ai_shop_daily_ceiling(client, tenant, monkeypatch):
    from backend.services import ai
    monkeypatch.setattr(ai, "AI_ORG_PER_DAY", 3)
    for _ in range(3):
        assert (await client.post("/api/ai/recognize-items", json={"text": "x"},
                                  headers=tenant)).status_code == 200
    r = await client.post("/api/ai/recognize-items", json={"text": "x"}, headers=tenant)
    assert r.status_code == 429 and "today" in r.json()["detail"]


async def test_extract_text_size_capped(client, tenant):
    r = await client.post("/api/inventory/postings/extract", data={"text": "x" * 20001},
                          headers=tenant)
    assert r.status_code == 413


async def test_extract_image_size_capped(client, tenant):
    from backend.services.ai import MAX_IMAGE_BYTES
    assert len(base64.b64encode(b"0" * MAX_IMAGE_BYTES)) <= 5 * 1024 * 1024
    big = b"\xff\xd8" + b"0" * MAX_IMAGE_BYTES
    r = await client.post("/api/inventory/postings/extract",
                          files={"file": ("inv.jpg", big, "image/jpeg")}, headers=tenant)
    assert r.status_code == 413


@pytest.mark.parametrize("field", ["prompt"])
async def test_document_ai_generate_prompt_capped(client, tenant, field):
    r = await client.post("/api/documents/ai-generate", json={field: "x" * 4001}, headers=tenant)
    assert r.status_code == 422
