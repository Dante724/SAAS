"""Build 110 — production hardening: webhook auth + rate limiting."""
import pytest

from backend.core.webhook_security import SlidingWindowLimiter, lookup_limiter


async def _me(client, H):
    return (await client.get("/api/auth/me", headers=H)).json()


async def _secret(client, H):
    return (await client.get("/api/settings/webhook-secret", headers=H)).json()["webhook_secret"]


@pytest.mark.asyncio
async def test_inbox_webhook_rejects_missing_or_wrong_token(client, tenant):
    org = (await _me(client, tenant))["org_id"]
    await _secret(client, tenant)  # ensure a secret exists (fail-closed from here on)

    no_token = await client.post(f"/api/inbox/webhook/{org}", json={
        "channel": "sms", "handle": "+1999", "body": "spoof"})
    assert no_token.status_code == 401

    wrong = await client.post(f"/api/inbox/webhook/{org}", json={
        "channel": "sms", "handle": "+1999", "body": "spoof"},
        headers={"X-Webhook-Token": "guess"})
    assert wrong.status_code == 401

    right = await client.post(f"/api/inbox/webhook/{org}", json={
        "channel": "sms", "handle": "+1999", "body": "real"},
        headers={"X-Webhook-Token": await _secret(client, tenant)})
    assert right.status_code == 201


@pytest.mark.asyncio
async def test_unknown_org_is_404_not_401(client):
    """Probing with a fabricated org id must not reveal whether ids exist."""
    r = await client.post("/api/inbox/webhook/not-a-real-org", json={
        "channel": "sms", "handle": "+1", "body": "x"})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_comms_webhook_token_gated(client, tenant):
    org = (await _me(client, tenant))["org_id"]
    sec = await _secret(client, tenant)

    spoof = await client.post(f"/api/comms/calls/webhook?org_id={org}", json={
        "provider": "x", "direction": "inbound", "from_number": "+1", "to_number": "+2",
        "status": "completed"})
    assert spoof.status_code == 401

    ok = await client.post(f"/api/comms/calls/webhook?org_id={org}", json={
        "provider": "x", "direction": "inbound", "from_number": "+1", "to_number": "+2",
        "status": "completed"}, headers={"X-Webhook-Token": sec})
    assert ok.status_code == 201


@pytest.mark.asyncio
async def test_secret_rotation_invalidates_old_token(client, tenant):
    org = (await _me(client, tenant))["org_id"]
    old = await _secret(client, tenant)
    new = (await client.post("/api/settings/webhook-secret/rotate",
                             headers=tenant)).json()["webhook_secret"]
    assert new != old

    stale = await client.post(f"/api/inbox/webhook/{org}", json={
        "channel": "sms", "handle": "+1", "body": "x"},
        headers={"X-Webhook-Token": old})
    assert stale.status_code == 401

    fresh = await client.post(f"/api/inbox/webhook/{org}", json={
        "channel": "sms", "handle": "+1", "body": "x"},
        headers={"X-Webhook-Token": new})
    assert fresh.status_code == 201


@pytest.mark.asyncio
async def test_lookup_rate_limited(client, tenant):
    """Eleventh rapid guess against one order number gets throttled."""
    lookup_limiter.reset()
    try:
        for _ in range(10):
            r = await client.post("/api/public/lookup", json={
                "order_number": "BRUTE-1", "phone": "5550001111"})
            assert r.status_code == 404          # unknown order, but allowed through
        blocked = await client.post("/api/public/lookup", json={
            "order_number": "BRUTE-1", "phone": "5550002222"})
        assert blocked.status_code == 429
    finally:
        lookup_limiter.reset()                    # don't poison other tests


def test_sliding_window_expires():
    lim = SlidingWindowLimiter(limit=2, window_s=0.05)
    assert lim.check("k") and lim.check("k")
    assert not lim.check("k")                     # third within window blocked
    import time
    time.sleep(0.06)
    assert lim.check("k")                         # window slid — allowed again
