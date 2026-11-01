"""A6 — Gmail/Outlook email connector (gated OAuth seam)."""
import pytest


@pytest.mark.asyncio
async def test_status_and_gated_connect(client, tenant):
    st = (await client.get("/api/integrations/email/status", headers=tenant)).json()
    # not configured on the server by default → both providers show configured:false
    assert st["providers"]["gmail"]["configured"] is False
    assert st["providers"]["outlook"]["configured"] is False
    assert st["accounts"] == []

    # connect is gated: without client creds it must 400, not fire an OAuth flow
    r = await client.post("/api/integrations/email/connect", json={"provider": "gmail"}, headers=tenant)
    assert r.status_code == 400
    bad = await client.post("/api/integrations/email/connect", json={"provider": "nope"}, headers=tenant)
    assert bad.status_code == 422


@pytest.mark.asyncio
async def test_connect_callback_flow_with_configured_provider(client, tenant, db, monkeypatch):
    """With client creds set, connect returns an authorize URL and the callback
    (stubbed token exchange) marks the account connected + default."""
    from backend.config import settings
    monkeypatch.setattr(settings, "GMAIL_CLIENT_ID", "cid")
    monkeypatch.setattr(settings, "GMAIL_CLIENT_SECRET", "csecret")

    conn = await client.post("/api/integrations/email/connect",
                            json={"provider": "gmail", "sender_name": "Acme Repairs"}, headers=tenant)
    assert conn.status_code == 200
    url = conn.json()["authorize_url"]
    assert url.startswith("https://accounts.google.com/") and "state=" in url

    # extract the state we generated (conn_id:nonce)
    from urllib.parse import parse_qs, urlparse
    state = parse_qs(urlparse(url).query)["state"][0]

    # stub the token exchange so no real network call happens
    async def _fake_exchange(provider, code):
        return {"access_token": "at", "refresh_token": "rt", "expires_in": 3600}
    monkeypatch.setattr("backend.routers.integrations_email.exchange_code", _fake_exchange)

    cb = await client.get(f"/api/integrations/email/callback?state={state}&code=authcode")
    assert cb.status_code == 200 and cb.json()["connected"] is True

    st = (await client.get("/api/integrations/email/status", headers=tenant)).json()
    acct = st["accounts"][0]
    assert acct["provider"] == "gmail" and acct["connected"] is True and acct["is_default"] is True
    assert acct["sender_name"] == "Acme Repairs"


@pytest.mark.asyncio
async def test_send_seam_uses_connection(client, tenant, db, monkeypatch):
    """The notification seam routes through a connected account when present."""
    from backend.models import EmailConnection
    from backend.services import notifications
    me = (await client.get("/api/auth/me", headers=tenant)).json()

    conn = EmailConnection(org_id=me["org_id"], provider="gmail", email_address="shop@acme.test",
                           access_token="at", connected=True, is_default=True)
    db.add(conn)
    await db.commit()

    sent = {}

    async def _fake_send(c, *, recipient, subject, body, html=False):
        sent.update(provider=c.provider, recipient=recipient)
        return {"status": "sent", "error": None}
    monkeypatch.setattr("backend.services.email_oauth.send_via_connection", _fake_send)

    res = await notifications.try_oauth_email(db, me["org_id"],
                                              recipient="cust@x.dev", subject="Ready", body="Your repair is ready")
    assert res == {"status": "sent", "error": None}
    assert sent["provider"] == "gmail" and sent["recipient"] == "cust@x.dev"

    # no connection for a different org → returns None (falls back to SMTP/simulated)
    none_res = await notifications.try_oauth_email(db, "other-org", recipient="x@x.dev", subject="s", body="b")
    assert none_res is None


@pytest.mark.asyncio
async def test_callback_encrypts_tokens_and_fetches_email(client, tenant, db, monkeypatch):
    """When a key is configured, the callback stores encrypted tokens (not
    plaintext) and pulls the account's own address from the provider."""
    from cryptography.fernet import Fernet
    from sqlalchemy import select

    from backend.config import settings
    from backend.models import EmailConnection
    monkeypatch.setattr(settings, "GMAIL_CLIENT_ID", "cid")
    monkeypatch.setattr(settings, "GMAIL_CLIENT_SECRET", "csecret")
    monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())

    conn = await client.post("/api/integrations/email/connect",
                             json={"provider": "gmail"}, headers=tenant)
    from urllib.parse import parse_qs, urlparse
    state = parse_qs(urlparse(conn.json()["authorize_url"]).query)["state"][0]

    async def _fake_exchange(provider, code):
        return {"access_token": "plain-at", "refresh_token": "plain-rt", "expires_in": 3600}
    async def _fake_fetch(provider, access_token):
        return "shop@gmail.test"
    monkeypatch.setattr("backend.routers.integrations_email.exchange_code", _fake_exchange)
    monkeypatch.setattr("backend.services.email_oauth.fetch_email", _fake_fetch)

    cb = await client.get(f"/api/integrations/email/callback?state={state}&code=authcode")
    assert cb.status_code == 200

    row = (await db.execute(select(EmailConnection).where(
        EmailConnection.org_id == (await client.get("/api/auth/me", headers=tenant)).json()["org_id"]))).scalars().first()
    await db.refresh(row)
    assert row.email_address == "shop@gmail.test"          # fetched from provider
    assert row.access_token.startswith("enc:v1:")          # encrypted at rest
    assert "plain-at" not in row.access_token
    from backend.services.crypto import decrypt
    assert decrypt(row.access_token) == "plain-at"         # round-trips


@pytest.mark.asyncio
async def test_set_default_switches_account(client, tenant, db):
    from backend.models import EmailConnection
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    a = EmailConnection(org_id=me["org_id"], provider="gmail", email_address="a@x.test",
                        access_token="at", connected=True, is_default=True)
    b = EmailConnection(org_id=me["org_id"], provider="outlook", email_address="b@x.test",
                        access_token="bt", connected=True, is_default=False)
    db.add_all([a, b])
    await db.commit()

    r = await client.post(f"/api/integrations/email/{b.id}/default", json={}, headers=tenant)
    assert r.status_code == 200
    st = (await client.get("/api/integrations/email/status", headers=tenant)).json()
    by_id = {x["id"]: x for x in st["accounts"]}
    assert by_id[b.id]["is_default"] is True
    assert by_id[a.id]["is_default"] is False


@pytest.mark.asyncio
async def test_notify_prefers_oauth_then_falls_back(client, tenant, db, monkeypatch):
    """The WO Message button sends through the connected account first
    (delivery == 'oauth'); with no account it falls back (delivery != 'oauth')."""
    from backend.models import EmailConnection
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()
    loc = (loc if isinstance(loc, list) else loc.get("items", loc))[0]["id"]
    cid = (await client.post("/api/clients", json={"name": "Cust", "phone": "+12", "email": "cust@x.test"},
                             headers=tenant)).json()["id"]
    oid = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc,
                             "complaint": "x", "lines": []}, headers=tenant)).json()["id"]

    # no connection → fallback path (simulated/smtp), never 'oauth'
    r0 = await client.post(f"/api/orders/{oid}/notify",
                           json={"channel": "email", "subject": "Hi", "body": "Ready"}, headers=tenant)
    assert r0.status_code == 200 and r0.json()["delivery"] != "oauth"

    # connected default account → oauth path
    db.add(EmailConnection(org_id=me["org_id"], provider="gmail", email_address="shop@x.test",
                           access_token="at", connected=True, is_default=True))
    await db.commit()

    async def _fake_send(c, *, recipient, subject, body, html=False):
        return {"status": "sent", "error": None}
    monkeypatch.setattr("backend.services.email_oauth.send_via_connection", _fake_send)

    r1 = await client.post(f"/api/orders/{oid}/notify",
                           json={"channel": "email", "subject": "Hi", "body": "Ready"}, headers=tenant)
    assert r1.status_code == 200 and r1.json()["delivery"] == "oauth"
