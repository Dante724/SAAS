"""Small platform leaks (security phase 4, audit L7–L11)."""
import logging

import pytest

from backend.config import settings


@pytest.mark.asyncio
async def test_metrics_needs_the_scrape_token_when_configured(client, monkeypatch):
    monkeypatch.setattr(settings, "METRICS_TOKEN", "scrape-secret")
    assert (await client.get("/metrics")).status_code == 401
    assert (await client.get("/metrics", headers={"Authorization": "Bearer wrong"})).status_code == 401
    r = await client.get("/metrics", headers={"Authorization": "Bearer scrape-secret"})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_metrics_hidden_when_deployed_without_a_token(client, monkeypatch):
    monkeypatch.setattr(settings, "METRICS_TOKEN", "")
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    assert (await client.get("/metrics")).status_code == 404


@pytest.mark.asyncio
async def test_hsts_only_when_deployed(client, monkeypatch):
    assert "strict-transport-security" not in (await client.get("/api/health")).headers
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    r = await client.get("/api/health")
    assert "max-age=" in r.headers.get("strict-transport-security", "")


@pytest.mark.asyncio
async def test_unsafe_request_id_is_replaced(client):
    ok = await client.get("/api/health", headers={"x-request-id": "trace-abc.123"})
    assert ok.headers["x-request-id"] == "trace-abc.123"
    bad = await client.get("/api/health", headers={"x-request-id": "x" * 500})
    assert len(bad.headers["x-request-id"]) <= 64 and bad.headers["x-request-id"] != "x" * 500


def test_path_tokens_are_redacted_in_logs_but_uuids_stay():
    from backend.core.logging import RedactionFilter
    rec = logging.LogRecord("repairos.access", logging.INFO, __file__, 1,
                            "%s %s -> %s", ("GET", "/api/portal/Zq3xV9pL0aBc7dEf1gHiJk/orders/"
                                            "1b4e28ba-2fa1-11d2-883f-0016d3cca427", 200), None)
    RedactionFilter().filter(rec)
    msg = rec.getMessage()
    assert "Zq3xV9pL0aBc7dEf1gHiJk" not in msg
    assert "1b4e28ba-2fa1-11d2-883f-0016d3cca427" in msg
    rec2 = logging.LogRecord("repairos.access", logging.INFO, __file__, 1, "%s", (
        "GET /api/settings/asset-identifier-rules -> 200",), None)
    RedactionFilter().filter(rec2)
    assert "asset-identifier-rules" in rec2.getMessage()
