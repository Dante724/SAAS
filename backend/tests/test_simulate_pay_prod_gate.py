"""Security regression: the unauthenticated simulate-pay endpoint (a dev/test affordance)
must be disabled in production, so a guessed intent id can't mark an invoice paid for free."""
import pytest


@pytest.mark.asyncio
async def test_simulate_pay_blocked_in_production(client, monkeypatch):
    from backend.config import settings
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    # the prod gate fires before the intent lookup, so even a missing id returns 403
    r = await client.post("/api/payments/intents/does-not-exist/simulate-pay")
    assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_simulate_pay_allowed_outside_production(client):
    # non-prod: gate does not fire; missing intent → 404 (not 403)
    r = await client.post("/api/payments/intents/does-not-exist/simulate-pay")
    assert r.status_code == 404, r.text
