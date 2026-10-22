"""Team C — integration review fixes: S2 (Meta tenant binding + per-org verify token),
S4 (Twilio SMS signature behind a proxy), S7 (no billed Twilio texts outside production),
P9 (opc1 grants can_manage_chats only to roles that reply to customers)."""
import hashlib
import hmac
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from backend.config import settings
from backend.services import messaging, org_creds, twilio_voice

REPO = Path(__file__).resolve().parents[2]
GLOBAL_SECRET = "operator-app-secret"


async def _org(client, H):
    return (await client.get("/api/auth/me", headers=H)).json()["org_id"]


async def _other(client):
    import uuid
    r = await client.post("/api/auth/register", json={
        "org_name": "Other", "name": "B", "email": f"b-{uuid.uuid4().hex[:8]}@t.dev", "password": "pw-test-12345"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _signed(payload: dict, secret: str) -> tuple[bytes, dict]:
    raw = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return raw, {"X-Hub-Signature-256": sig, "Content-Type": "application/json"}


def _messenger(page_id: str, mid: str) -> dict:
    return {"object": "page", "entry": [{"id": page_id, "messaging": [
        {"sender": {"id": "psid-1"}, "recipient": {"id": page_id}, "message": {"mid": mid, "text": "hi"}}]}]}


# ── S2 ───────────────────────────────────────────────────────────────────────

async def test_global_meta_secret_is_refused_for_a_shop_with_no_registered_ids(client, tenant, monkeypatch):
    monkeypatch.setattr(settings, "META_APP_SECRET", GLOBAL_SECRET)
    org = await _org(client, tenant)
    raw, h = _signed(_messenger("PAGE-X", "m-1"), GLOBAL_SECRET)
    r = await client.post(f"/api/chats/webhook/meta/{org}", content=raw, headers=h)
    assert r.status_code == 403


async def test_operator_signed_delivery_cannot_be_replayed_into_another_shop(client, tenant, db, monkeypatch):
    monkeypatch.setattr(settings, "META_APP_SECRET", GLOBAL_SECRET)
    a = await _org(client, tenant)
    b = await _org(client, await _other(client))
    await org_creds.set_creds(db, a, "meta", {"page_id": "PAGE-A"})
    await org_creds.set_creds(db, b, "meta", {"page_id": "PAGE-B"})
    raw, h = _signed(_messenger("PAGE-A", "m-2"), GLOBAL_SECRET)
    assert (await client.post(f"/api/chats/webhook/meta/{a}", content=raw, headers=h)).json()["ingested"] == 1
    replay = await client.post(f"/api/chats/webhook/meta/{b}", content=raw, headers=h)
    assert replay.status_code == 201 and replay.json()["ingested"] == 0
    # same rule through the old (forwarding) URL
    assert (await client.post(f"/api/inbox/webhook/meta/{b}", content=raw, headers=h)).json()["ingested"] == 0


async def test_whatsapp_events_bind_on_phone_number_id(client, tenant, db, monkeypatch):
    monkeypatch.setattr(settings, "META_APP_SECRET", GLOBAL_SECRET)
    org = await _org(client, tenant)
    await org_creds.set_creds(db, org, "meta", {"whatsapp_phone_id": "WA-1"})
    body = {"object": "whatsapp_business_account", "entry": [{"id": "WABA", "changes": [{"value": {
        "metadata": {"phone_number_id": "{}"}, "messages": [{"from": "15550001", "id": "{}", "text": {"body": "x"}}]}}]}]}
    mine = json.loads(json.dumps(body).replace('"{}"', '"WA-1"', 1).replace('"{}"', '"wamid.1"', 1))
    theirs = json.loads(json.dumps(body).replace('"{}"', '"WA-OTHER"', 1).replace('"{}"', '"wamid.2"', 1))
    for payload, want in ((mine, 1), (theirs, 0)):
        raw, h = _signed(payload, GLOBAL_SECRET)
        assert (await client.post(f"/api/chats/webhook/meta/{org}", content=raw, headers=h)).json()["ingested"] == want


async def test_shops_own_app_secret_still_works_without_ids(client, tenant, db, monkeypatch):
    monkeypatch.setattr(settings, "META_APP_SECRET", GLOBAL_SECRET)
    org = await _org(client, tenant)
    await org_creds.set_creds(db, org, "meta", {"app_secret": "shop-secret"})
    raw, h = _signed(_messenger("ANY", "m-3"), "shop-secret")
    assert (await client.post(f"/api/chats/webhook/meta/{org}", content=raw, headers=h)).json()["ingested"] == 1
    raw, h = _signed(_messenger("ANY", "m-4"), GLOBAL_SECRET)
    assert (await client.post(f"/api/chats/webhook/meta/{org}", content=raw, headers=h)).status_code == 403


async def test_meta_handshake_uses_the_shops_own_verify_token(client, tenant, db, monkeypatch):
    monkeypatch.setattr(settings, "META_VERIFY_TOKEN", "operator-token")
    org = await _org(client, tenant)
    await org_creds.set_creds(db, org, "meta", {"verify_token": "shop-token"})
    q = {"hub.mode": "subscribe", "hub.challenge": "42"}
    ok = await client.get(f"/api/chats/webhook/meta/{org}", params={**q, "hub.verify_token": "shop-token"})
    assert ok.status_code == 200 and ok.text == "42"
    other = await client.get(f"/api/chats/webhook/meta/{org}", params={**q, "hub.verify_token": "operator-token"})
    assert other.status_code == 403
    old_url = await client.get(f"/api/inbox/webhook/meta/{org}", params={**q, "hub.verify_token": "shop-token"})
    assert old_url.status_code == 200


# ── S4 ───────────────────────────────────────────────────────────────────────

async def test_twilio_sms_signature_accepts_the_public_url_behind_a_proxy(client, tenant, db, monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://shop.example.com")
    org = await _org(client, tenant)
    await org_creds.set_creds(db, org, "twilio", {"account_sid": "ACtest", "auth_token": "tw-token"})
    form = {"From": "+15557770000", "Body": "STOP", "MessageSid": "SM-proxy-1"}
    path = f"/api/chats/webhook/twilio?org_id={org}"
    sig = twilio_voice.signature("tw-token", "https://shop.example.com" + path, list(form.items()))
    r = await client.post(path, data=form, headers={"X-Twilio-Signature": sig})
    assert r.status_code == 200, r.text
    bad = await client.post(path, data={**form, "MessageSid": "SM-proxy-2"}, headers={"X-Twilio-Signature": "nope"})
    assert bad.status_code == 403


# ── S7 ───────────────────────────────────────────────────────────────────────

class _NoNetwork:
    def __init__(self, *a, **k):
        raise AssertionError("a real Twilio request was attempted")


async def test_live_twilio_texts_are_refused_outside_production(monkeypatch):
    from backend.services.notifications import TwilioAdapter
    monkeypatch.setattr(httpx, "AsyncClient", _NoNetwork)
    creds = {"account_sid": "AClive", "auth_token": "t", "from": "+15551234567", "whatsapp_from": "+15551234567"}
    for ch in ("sms", "whatsapp"):
        r = await TwilioAdapter(ch, creds).send(recipient="+15550000001", subject=None, body="hi")
        assert r["status"] == "failed" and "ALLOW_LIVE_TELEPHONY" in r["error"], (ch, r)
    for ch in ("sms", "whatsapp"):
        r = await messaging.send(ch, "+15550000001", "hi", creds)
        assert r["status"] == "failed" and "ALLOW_LIVE_TELEPHONY" in r["error"], (ch, r)


async def test_twilio_test_number_and_production_opt_in_still_send(monkeypatch):
    from backend.services.notifications import TwilioAdapter
    sent = []

    class _Fake:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **kw):
            sent.append(url)
            return httpx.Response(201, json={"sid": "SM1"})
    monkeypatch.setattr(httpx, "AsyncClient", _Fake)
    test_creds = {"account_sid": "ACtest", "auth_token": "t", "from": twilio_voice.TEST_FROM_NUMBER}
    assert (await TwilioAdapter("sms", test_creds).send(recipient="+1", subject=None, body="x"))["status"] == "sent"
    monkeypatch.setattr(twilio_voice, "live_allowed", lambda: True)
    live = {"account_sid": "AClive", "auth_token": "t", "from": "+15551234567"}
    assert (await messaging.send("sms", "+1", "x", live))["status"] == "sent"
    assert len(sent) == 2


# ── P9 ───────────────────────────────────────────────────────────────────────

def _alembic(db_path: Path, target: str) -> None:
    env = dict(os.environ, ENVIRONMENT="test", SEED_DEMO="false", DATABASE_URL=f"sqlite+aiosqlite:///{db_path}")
    env.pop("TEST_DATABASE_URL", None)
    r = subprocess.run([sys.executable, "-m", "alembic", "-c", "backend/alembic.ini", "upgrade", target],
                       cwd=REPO, env=env, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr[-2000:]


def test_opc1_grants_can_manage_chats_only_to_roles_that_reply(tmp_path):
    db = tmp_path / "p9.db"
    _alembic(db, "ope1")                                 # opc1's parent
    con = sqlite3.connect(db)
    rows = [("replier", {"can_reply_customer": True}), ("tech", {"can_reply_customer": False}),
            ("silent", {"can_view_reports": True}), ("denied", {"can_reply_customer": True, "can_manage_chats": False})]
    for rid, perms in rows:
        con.execute("INSERT INTO roles (id, org_id, name, permissions, perms_explicit, location_scope) "
                    "VALUES (?, 'o1', ?, ?, 1, 'all')", (rid, rid, json.dumps(perms)))
    con.commit()
    con.close()
    _alembic(db, "opc1")
    con = sqlite3.connect(db)
    got = {rid: json.loads(p) for rid, p in con.execute("SELECT id, permissions FROM roles")}
    con.close()
    assert got["replier"]["can_manage_chats"] is True
    assert "can_manage_chats" not in got["tech"]
    assert "can_manage_chats" not in got["silent"]
    assert got["denied"]["can_manage_chats"] is False    # an explicit deny is kept
