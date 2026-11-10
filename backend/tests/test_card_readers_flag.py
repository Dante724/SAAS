"""Card readers are switched off (CARD_READERS_ENABLED, default False): the shop has no reader and
can't connect one. Every /api/payments/terminal/* route answers 404, the app is told through
/auth/me so it hides every reader entry point, and nothing is dropped: the tables, the data and the
provider webhooks stay, so turning the switch on brings it all back."""
import pytest

from backend.config import settings

OFF = "Card readers aren't available"
ROUTES = [
    ("GET", "/api/payments/terminal/config", None),
    ("GET", "/api/payments/terminal/readers", None),
    ("POST", "/api/payments/terminal/readers", {"provider": "simulated", "label": "Desk"}),
    ("DELETE", "/api/payments/terminal/readers/x", None),
    ("POST", "/api/payments/terminal/readers/x/clear", {}),
    ("POST", "/api/payments/terminal/charge", {"terminal_id": "x", "order_id": "o"}),
    ("GET", "/api/payments/terminal/charges", None),
    ("GET", "/api/payments/terminal/charges/x", None),
    ("POST", "/api/payments/terminal/charges/x/cancel", {}),
    ("POST", "/api/payments/terminal/charges/x/refund", {}),
    ("POST", "/api/payments/terminal/charges/x/simulate", {"outcome": "approve"}),
]


async def _call(client, h, method, url, body):
    return await client.request(method, url, json=body, headers=h) if body is not None else \
        await client.request(method, url, headers=h)


def test_card_readers_are_off_by_default():
    assert settings.CARD_READERS_ENABLED is False


@pytest.mark.asyncio
async def test_every_reader_route_is_404_when_off(client, tenant, monkeypatch):
    monkeypatch.setattr(settings, "CARD_READERS_ENABLED", False)
    for method, url, body in ROUTES:
        r = await _call(client, tenant, method, url, body)
        assert r.status_code == 404, (method, url, r.status_code, r.text)
        assert r.json()["detail"] == OFF, (method, url, r.text)
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    assert me["card_readers"] is False


@pytest.mark.asyncio
async def test_reader_routes_work_when_on(client, tenant, monkeypatch):
    monkeypatch.setattr(settings, "CARD_READERS_ENABLED", True)
    assert (await client.get("/api/payments/terminal/readers", headers=tenant)).status_code == 200
    r = await client.post("/api/payments/terminal/readers", json={"provider": "simulated", "label": "Desk"},
                          headers=tenant)
    assert r.status_code == 201, r.text
    assert (await client.get("/api/payments/terminal/charges", headers=tenant)).status_code == 200
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    assert me["card_readers"] is True


@pytest.mark.asyncio
async def test_off_still_needs_a_login(client, monkeypatch):
    """The switch doesn't open anything: without a session the routes still refuse (401/404)."""
    monkeypatch.setattr(settings, "CARD_READERS_ENABLED", False)
    r = await client.get("/api/payments/terminal/readers")
    assert r.status_code in (401, 404)


@pytest.mark.asyncio
async def test_provider_webhooks_stay_when_off(client, monkeypatch):
    """Webhooks aren't reader UI: a charge started before the switch went off must still settle."""
    monkeypatch.setattr(settings, "CARD_READERS_ENABLED", False)
    r = await client.post("/api/payments/stripe/webhook", content=b"{}", headers={"content-type": "application/json"})
    assert r.status_code != 404 or r.json().get("detail") != OFF, r.text
