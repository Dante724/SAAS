"""A4 Xero (gated) · A7 VoIP+campaigns · SSO groundwork."""
import pytest


# ── A4 Xero — gated: status works, connect refuses without creds ─────────────
@pytest.mark.asyncio
async def test_xero_status_and_gated_connect(client, tenant):
    st = (await client.get("/api/integrations/xero/status", headers=tenant)).json()
    assert "connected" in st
    # not configured → connect is refused with a clear message
    r = await client.get("/api/integrations/xero/connect?redirect_uri=https://app/cb", headers=tenant)
    assert r.status_code == 400 and "not configured" in r.json()["detail"].lower()


async def _wh_tok(client, H):
    sec = (await client.get("/api/settings/webhook-secret", headers=H)).json()["webhook_secret"]
    return {"X-Webhook-Token": sec}


# ── A7 VoIP — webhook records + client-matches ───────────────────────────────
@pytest.mark.asyncio
async def test_voip_webhook_matches_client(client, tenant):
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    # a client with a known number
    await client.post("/api/clients", json={"name": "Caller", "phone": "+15551234567"}, headers=tenant)
    wh = await client.post(f"/api/comms/calls/webhook?org_id={me['org_id']}", json={
        "provider": "twilio", "direction": "inbound",
        "from_number": "+15551234567", "to_number": "+15550000000", "status": "completed", "duration_s": 42}, headers=await _wh_tok(client, tenant))
    assert wh.status_code == 201 and wh.json()["matched"] is True

    calls = (await client.get("/api/comms/calls", headers=tenant)).json()["items"]
    assert any(c["from"] == "+15551234567" and c["client_id"] for c in calls)

    # unknown number → recorded but unmatched
    wh2 = await client.post(f"/api/comms/calls/webhook?org_id={me['org_id']}", json={
        "provider": "twilio", "direction": "inbound", "from_number": "+19999999999"}, headers=await _wh_tok(client, tenant))
    assert wh2.status_code == 201 and wh2.json()["matched"] is False


# ── A7 Campaigns — create + segmented send ───────────────────────────────────
@pytest.mark.asyncio
async def test_campaign_create_and_send_segment(client, tenant):
    # two loyalty clients with phones, one standard
    await client.post("/api/clients", json={"name": "Gold", "phone": "+1500", "loyalty_tier": "gold"}, headers=tenant)
    await client.post("/api/clients", json={"name": "Silver", "phone": "+1501", "loyalty_tier": "silver"}, headers=tenant)
    await client.post("/api/clients", json={"name": "Plain", "phone": "+1502", "loyalty_tier": "standard"}, headers=tenant)

    c = await client.post("/api/comms/campaigns", json={
        "name": "VIP promo", "channel": "sms", "body": "10% off!", "segment": "loyalty"}, headers=tenant)
    assert c.status_code == 201
    cid = c.json()["id"]

    send = await client.post(f"/api/comms/campaigns/{cid}/send", headers=tenant)
    assert send.status_code == 200
    assert send.json()["recipients"] == 2          # only loyalty tier, both have phones
    # can't send twice
    again = await client.post(f"/api/comms/campaigns/{cid}/send", headers=tenant)
    assert again.status_code == 400

    listing = (await client.get("/api/comms/campaigns", headers=tenant)).json()["items"]
    assert any(x["id"] == cid and x["status"] == "sent" and x["recipients"] == 2 for x in listing)


# ── SSO groundwork — config, discovery, gated authorize ──────────────────────
@pytest.mark.asyncio
async def test_sso_config_discovery_and_authorize(client, tenant):
    created = await client.post("/api/sso/config", json={
        "protocol": "oidc", "display_name": "Acme SSO",
        "issuer": "https://idp.acme.test", "client_id": "abc123",
        "client_secret": "shh", "email_domain": "acme.test", "enabled": True}, headers=tenant)
    assert created.status_code == 201
    assert created.json()["client_secret"] == "***"        # secret redacted in responses
    cid = created.json()["id"]

    # discovery by email domain (unauth)
    disc = (await client.get("/api/sso/discover?email=jane@acme.test")).json()
    assert disc["sso"] is True and disc["protocol"] == "oidc" and disc["provider_config_id"] == cid
    miss = (await client.get("/api/sso/discover?email=bob@other.test")).json()
    assert miss["sso"] is False

    # gated authorize returns the IdP URL a real flow would use
    auth = (await client.get(f"/api/sso/authorize/{cid}?redirect_uri=https://app/cb")).json()
    assert auth["ready"] is True and auth["authorization_url"].startswith("https://idp.acme.test/authorize?")

    assert (await client.delete(f"/api/sso/config/{cid}", headers=tenant)).status_code == 200
