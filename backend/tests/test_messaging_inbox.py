"""Build 144af — WhatsApp / Messenger / Instagram into the inbox."""
import pytest

from backend.config import settings
from backend.services import messaging


def test_parse_meta_webhook_messenger_and_whatsapp():
    messenger = {"object": "page", "entry": [
        {"messaging": [{"sender": {"id": "u1"}, "message": {"mid": "m1", "text": "hello"}}]}]}
    out = messaging.parse_meta_webhook(messenger)
    assert out and out[0]["channel"] == "facebook" and out[0]["handle"] == "u1" and out[0]["body"] == "hello"

    ig = {"object": "instagram", "entry": [
        {"messaging": [{"sender": {"id": "ig9"}, "message": {"mid": "m2", "text": "hi ig"}}]}]}
    assert messaging.parse_meta_webhook(ig)[0]["channel"] == "instagram"

    wa = {"object": "whatsapp_business_account", "entry": [
        {"changes": [{"value": {"messages": [{"from": "15551234", "id": "wamid.1",
                                              "text": {"body": "part ready?"}}]}}]}]}
    o = messaging.parse_meta_webhook(wa)[0]
    assert o["channel"] == "whatsapp" and o["handle"] == "15551234" and o["body"] == "part ready?"


@pytest.mark.asyncio
async def test_send_is_simulated_without_credentials():
    for ch in ("whatsapp", "sms", "facebook", "instagram"):
        r = await messaging.send(ch, "u1", "hi")
        assert r["status"] == "simulated", (ch, r)
    assert (await messaging.send("whatsapp", "", "hi"))["status"] == "skipped"


@pytest.mark.asyncio
async def test_meta_webhook_verify(client, tenant, monkeypatch):
    org = (await client.get("/api/auth/me", headers=tenant)).json()["org_id"]
    monkeypatch.setattr(settings, "META_VERIFY_TOKEN", "s3cret")
    ok = await client.get(f"/api/inbox/webhook/meta/{org}",
                          params={"hub.mode": "subscribe", "hub.verify_token": "s3cret", "hub.challenge": "42"})
    assert ok.status_code == 200 and ok.text == "42"
    bad = await client.get(f"/api/inbox/webhook/meta/{org}",
                           params={"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "42"})
    assert bad.status_code == 403


@pytest.mark.asyncio
async def test_inbound_meta_creates_thread_and_reply_dispatches(client, tenant):
    org = (await client.get("/api/auth/me", headers=tenant)).json()["org_id"]
    payload = {"object": "page", "entry": [
        {"messaging": [{"sender": {"id": "fbuser1"}, "message": {"mid": "mm1", "text": "my phone won't charge"}}]}]}
    r = (await client.post(f"/api/inbox/webhook/meta/{org}", json=payload)).json()
    assert r["ingested"] == 1
    # de-dupe on repeat
    assert (await client.post(f"/api/inbox/webhook/meta/{org}", json=payload)).json()["ingested"] == 0

    threads = (await client.get("/api/inbox/threads?channel=facebook", headers=tenant)).json()["items"]
    assert threads and threads[0]["handle"] == "fbuser1"
    tid = threads[0]["id"]

    # replying dispatches through the (simulated) provider and records outbound
    res = (await client.post(f"/api/inbox/threads/{tid}/reply", json={"body": "We can fix that today."}, headers=tenant)).json()
    assert res["status"] == "simulated" and res["dispatched"] is True
    msgs = (await client.get(f"/api/inbox/threads/{tid}", headers=tenant)).json()["messages"]
    assert any(m["direction"] == "outbound" and "fix that today" in m["body"] for m in msgs)
