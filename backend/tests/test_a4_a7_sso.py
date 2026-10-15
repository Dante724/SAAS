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
async def test_sso_config_discovery_and_authorize(client, tenant, monkeypatch):
    from backend.config import settings
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://app.example")
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
    auth = (await client.get(f"/api/sso/authorize/{cid}?redirect_uri=https://app.example/sso/cb")).json()
    assert auth["ready"] is True and auth["authorization_url"].startswith("https://idp.acme.test/authorize?")

    assert (await client.delete(f"/api/sso/config/{cid}", headers=tenant)).status_code == 200


# ── SSO hardening (security phase 4) ─────────────────────────────────────────
async def _register(client, email):
    r = await client.post("/api/auth/register", json={
        "org_name": "Other shop", "name": "Other", "email": email, "password": "S3cure-pass-123!"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.mark.asyncio
async def test_sso_rejects_public_and_already_claimed_domains(client, tenant):
    r = await client.post("/api/sso/config", json={"email_domain": "gmail.com", "enabled": True}, headers=tenant)
    assert r.status_code == 422
    r = await client.post("/api/sso/config", json={"email_domain": "claimed.test", "enabled": True,
                                                   "issuer": "https://idp.claimed.test"}, headers=tenant)
    assert r.status_code == 201, r.text
    other = await _register(client, "owner@elsewhere.test")
    r = await client.post("/api/sso/config", json={"email_domain": "Claimed.TEST", "enabled": True}, headers=other)
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_sso_refuses_plain_http_idp_and_foreign_redirects(client, tenant, monkeypatch):
    from backend.config import settings
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://app.example")
    r = await client.post("/api/sso/config", json={"issuer": "http://idp.insecure.test"}, headers=tenant)
    assert r.status_code == 422
    cid = (await client.post("/api/sso/config", json={
        "issuer": "https://idp.redir.test", "client_id": "c", "enabled": True}, headers=tenant)).json()["id"]
    r = await client.get(f"/api/sso/authorize/{cid}?redirect_uri=https://evil.example/steal")
    assert r.status_code == 422
    ok = (await client.get(f"/api/sso/authorize/{cid}?redirect_uri=https://app.example/sso/cb")).json()
    assert "state=" in ok["authorization_url"]


@pytest.mark.asyncio
async def test_sso_client_secret_is_encrypted_at_rest(client, tenant, db, monkeypatch):
    from cryptography.fernet import Fernet
    from sqlalchemy import select
    from backend.config import settings
    from backend.models import SsoConnection
    from backend.services import crypto
    monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    cid = (await client.post("/api/sso/config", json={"client_secret": "top-secret-value"},
                             headers=tenant)).json()["id"]
    row = (await db.execute(select(SsoConnection).where(SsoConnection.id == cid))).scalar_one()
    assert row.client_secret != "top-secret-value"
    assert crypto.decrypt(row.client_secret) == "top-secret-value"
