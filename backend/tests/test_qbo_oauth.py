"""QuickBooks Online OAuth — the callback must complete without a Bearer token
(the browser follows Intuit's redirect), recovering org context from signed
state; tokens are encrypted; expired tokens refresh before a push; every sync
attempt is logged and visible.
"""
import pytest
from sqlalchemy import select

from backend.models import QboConnection
from backend.services.dates import now_utc


async def _configure(monkeypatch):
    from backend.config import settings
    monkeypatch.setattr(settings, "QBO_CLIENT_ID", "cid")
    monkeypatch.setattr(settings, "QBO_CLIENT_SECRET", "csecret")


async def _begin(client, tenant):
    r = await client.get("/api/integrations/qbo/connect", headers=tenant)
    assert r.status_code == 200, r.text
    from urllib.parse import parse_qs, urlparse
    return parse_qs(urlparse(r.json()["auth_url"]).query)["state"][0]


@pytest.mark.asyncio
async def test_callback_completes_without_bearer_token(client, tenant, db, monkeypatch):
    await _configure(monkeypatch)
    state = await _begin(client, tenant)

    async def _fake_exchange(code):
        return {"access_token": "qbo-at", "refresh_token": "qbo-rt", "expires_in": 3600}
    monkeypatch.setattr("backend.services.qbo._exchange_code", _fake_exchange)

    # NOTE: no auth headers — mirrors the real provider redirect
    cb = await client.get(f"/api/integrations/qbo/callback?code=ac&realmId=R123&state={state}")
    assert cb.status_code == 200, cb.text

    st = (await client.get("/api/integrations/qbo/status", headers=tenant)).json()
    assert st["connected"] is True

    me = (await client.get("/api/auth/me", headers=tenant)).json()
    conn = (await db.execute(select(QboConnection).where(QboConnection.org_id == me["org_id"]))).scalar_one()
    await db.refresh(conn)
    assert conn.realm_id == "R123"
    # token stored, not the plaintext state nonce reuse; decrypts back
    from backend.services.crypto import decrypt
    assert decrypt(conn.access_token) == "qbo-at"


@pytest.mark.asyncio
async def test_callback_rejects_tampered_state(client, tenant, db, monkeypatch):
    await _configure(monkeypatch)
    state = await _begin(client, tenant)
    tampered = state[:-1] + ("0" if state[-1] != "0" else "1")
    cb = await client.get(f"/api/integrations/qbo/callback?code=ac&realmId=R1&state={tampered}")
    assert cb.status_code == 400


@pytest.mark.asyncio
async def test_expired_token_refreshes_before_push(client, tenant, db, monkeypatch):
    """A push with an expired access token triggers a refresh; the refreshed
    (encrypted) token is persisted and the attempt is logged."""
    from datetime import datetime, timedelta
    await _configure(monkeypatch)
    me = (await client.get("/api/auth/me", headers=tenant)).json()

    from backend.services.crypto import decrypt, encrypt
    conn = QboConnection(org_id=me["org_id"], realm_id="R9", environment="sandbox", connected=True,
                         access_token=encrypt("old-at"), refresh_token=encrypt("old-rt"),
                         token_expires_at=now_utc() - timedelta(minutes=5))
    db.add(conn)
    # a contact to push
    cid = (await client.post("/api/clients", json={"name": "QBO Cust", "phone": "+15"}, headers=tenant)).json()["id"]
    await db.commit()

    refreshed = {"n": 0}
    async def _fake_refresh(db_, c):
        refreshed["n"] += 1
        c.access_token = encrypt("new-at")
        c.token_expires_at = now_utc() + timedelta(hours=1)
    async def _fake_push(c, entity_type, payload, remote_id):
        assert decrypt(c.access_token) == "new-at"      # push sees the refreshed token
        return "QB-1"
    monkeypatch.setattr("backend.services.qbo._refresh", _fake_refresh)
    monkeypatch.setattr("backend.services.qbo._push", _fake_push)

    r = await client.post(f"/api/integrations/qbo/sync/contact/{cid}", headers=tenant)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ok" and r.json()["remote_id"] == "QB-1"
    assert refreshed["n"] == 1

    logs = (await client.get("/api/integrations/qbo/logs", headers=tenant)).json()["items"]
    assert any(x["status"] == "ok" and x["remote_id"] == "QB-1" for x in logs)


@pytest.mark.asyncio
async def test_sync_skipped_logs_when_not_connected(client, tenant):
    cid = (await client.post("/api/clients", json={"name": "NC", "phone": "+16"}, headers=tenant)).json()["id"]
    r = await client.post(f"/api/integrations/qbo/sync/contact/{cid}", headers=tenant)
    assert r.status_code == 200 and r.json()["status"] == "skipped"
    logs = (await client.get("/api/integrations/qbo/logs", headers=tenant)).json()["items"]
    assert any(x["status"] == "skipped" for x in logs)
