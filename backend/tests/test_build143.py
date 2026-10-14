"""Build 143 — Phase 4 message templates + Phase 8 AI reply suggestion."""
import pytest


async def _client(client, h):
    return (await client.post("/api/clients", json={"name": "Comms", "phone": "+15550143"}, headers=h)).json()["id"]


@pytest.mark.asyncio
async def test_message_template_crud(client, tenant):
    r = await client.post("/api/clients/message-templates/all", json={
        "name": "Ready for pickup", "channel": "sms",
        "body": "Hi, your device is ready for pickup. Thanks!"}, headers=tenant)
    assert r.status_code == 201, r.text
    tid = r.json()["id"]

    items = (await client.get("/api/clients/message-templates/all", headers=tenant)).json()["items"]
    assert any(t["id"] == tid and t["name"] == "Ready for pickup" for t in items)

    d = await client.delete(f"/api/clients/message-templates/all/{tid}", headers=tenant)
    assert d.status_code == 200
    items2 = (await client.get("/api/clients/message-templates/all", headers=tenant)).json()["items"]
    assert all(t["id"] != tid for t in items2)


@pytest.mark.asyncio
async def test_message_template_validation(client, tenant):
    r = await client.post("/api/clients/message-templates/all",
                          json={"name": "", "body": ""}, headers=tenant)
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_ai_suggest_reply_never_autosends(client, tenant):
    cid = await _client(client, tenant)
    # log an inbound message so the AI has context
    await client.post(f"/api/clients/{cid}/communication",
                      json={"channel": "sms", "body": "Is my phone fixed yet?"}, headers=tenant)

    r = await client.post(f"/api/clients/{cid}/communication/suggest", json={}, headers=tenant)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ai_drafted"] is True
    assert body["provider"] in ("stub", "anthropic")
    assert isinstance(body["reply"], str) and body["reply"]  # a draft is returned...

    # ...but it is NOT auto-sent: the message history is unchanged by suggesting
    msgs = (await client.get(f"/api/clients/{cid}/communication", headers=tenant)).json()["items"]
    assert len(msgs) == 1   # only the one we logged, no AI-sent message
