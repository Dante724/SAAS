"""Team D — Twilio voice (D1), contacts from calls (D2), clients tag filter (D3).

Twilio's HTTP API is replaced by an httpx.MockTransport that mimics its documented
test-credential behaviour; nothing leaves the machine. A smoke test against the real Twilio
test API runs only when TWILIO_TEST_ACCOUNT_SID / TWILIO_TEST_AUTH_TOKEN are set.
"""
import base64
import hashlib
import hmac
import json
import os
from urllib.parse import parse_qs

import httpx
import pytest
from sqlalchemy import select

from backend.config import settings
from backend.models import Call, Client, Taggable, Task
from backend.services import org_creds, twilio_voice

SID = "AC" + "a" * 32
TOKEN = "tok_shop_a"
SID_B = "AC" + "b" * 32
TOKEN_B = "tok_shop_b"
NUMBER = "+15005550006"


# ── Twilio mock ──────────────────────────────────────────────────────────────
class FakeTwilio:
    """Accounts fetch: 403/20008 for test keys, 200 for live keys, 401 otherwise.
    Calls create: 201 with a fresh CallSid for any known keys."""

    def __init__(self, live=False):
        self.live = live
        self.calls = []
        self.n = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        auth = base64.b64decode(request.headers["authorization"].split()[1]).decode()
        sid, tok = auth.split(":", 1)
        if (sid, tok) not in ((SID, TOKEN), (SID_B, TOKEN_B)):
            return httpx.Response(401, json={"code": 20003, "message": "Authenticate"})
        if request.method == "GET" and request.url.path.endswith(f"/Accounts/{sid}.json"):
            if self.live:
                return httpx.Response(200, json={"sid": sid, "friendly_name": "Live shop"})
            return httpx.Response(403, json={"code": 20008, "message": "Test Account Credentials"})
        if request.method == "POST" and request.url.path.endswith("/Calls.json"):
            form = parse_qs(request.content.decode())
            self.calls.append(form)
            self.n += 1
            return httpx.Response(201, json={"sid": f"CA{self.n:032d}", "status": "queued"})
        return httpx.Response(404, json={"code": 20404})


@pytest.fixture
def fake_twilio(monkeypatch):
    fake = FakeTwilio()
    monkeypatch.setattr(twilio_voice, "transport", httpx.MockTransport(fake))
    return fake


def sign(token, url, form):
    payload = url + "".join(k + form[k] for k in sorted(form))
    return base64.b64encode(hmac.new(token.encode(), payload.encode(), hashlib.sha1).digest()).decode()


async def post_signed(client, path, form, token=TOKEN):
    return await client.post(path, data=form,
                             headers={"X-Twilio-Signature": sign(token, "http://test" + path, form)})


async def org_of(client, h):
    return (await client.get("/api/auth/me", headers=h)).json()["org_id"]


async def connect(client, h, sid=SID, token=TOKEN, forward=None, enable=True):
    r = await client.put("/api/telephony/twilio", json={"account_sid": sid, "auth_token": token}, headers=h)
    assert r.status_code == 200, r.text
    if enable:
        r = await client.put("/api/telephony/config", json={
            "provider": "twilio", "enabled": True, "caller_id_number": NUMBER,
            "forward_to": forward}, headers=h)
        assert r.status_code == 200, r.text
    return r.json()


async def second_tenant(client):
    import uuid
    r = await client.post("/api/auth/register", json={
        "org_name": "Other Co", "name": "Other", "email": f"o-{uuid.uuid4().hex[:8]}@test.dev",
        "password": "pw-test-12345"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def limited(client, h, email, deny=("can_manage_settings",), phone=None):
    role = await client.post("/api/settings/rbac/roles", json={
        "name": f"Limited {email}", "permissions": {k: False for k in deny}}, headers=h)
    body = {"name": "Limited", "email": email, "password": "pw-lim-12345", "role_id": role.json()["id"]}
    if phone:
        body["phone"] = phone
    await client.post("/api/staff/employees", json=body, headers=h)
    tok = await client.post("/api/auth/token", data={"username": email, "password": "pw-lim-12345"})
    return {"Authorization": f"Bearer {tok.json()['access_token']}"}


def inbound(sid="CA" + "1" * 32, frm="+14155550100", account=SID, to=NUMBER, **kw):
    return {"CallSid": sid, "AccountSid": account, "From": frm, "To": to,
            "CallStatus": "ringing", "Direction": "inbound", **kw}


async def set_my_phone(client, h, db, phone="+14155550111"):
    from backend.models import Employee
    me = (await client.get("/api/auth/me", headers=h)).json()
    emp = await db.get(Employee, me["id"])
    emp.phone = phone
    await db.commit()


async def enable_contacts(client, h, **cfg):
    body = {"enabled": True, "direction": "incoming", "tag_ids": [], "assign_manager": True, **cfg}
    r = await client.put("/api/telephony/automation/contacts", json=body, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


# ── D1: credentials ──────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_connect_test_creds_stored_encrypted_and_never_returned(client, tenant, fake_twilio, db):
    body = await connect(client, tenant)
    tw = body["twilio"]
    assert tw["connected"] and tw["mode"] == "test" and tw["test_mode"] is True
    assert "voice_url" in tw and "status_url" in tw
    blob = json.dumps((await client.get("/api/telephony/config", headers=tenant)).json())
    assert TOKEN not in blob and SID not in blob
    # not exposed through the generic integrations API either
    allc = json.dumps((await client.get("/api/integrations/credentials", headers=tenant)).json())
    assert TOKEN not in allc and "twilio_voice" not in allc
    r = await client.put("/api/integrations/credentials", json={
        "provider": "twilio_voice", "data": {"account_sid": SID, "auth_token": "x", "mode": "test"}}, headers=tenant)
    assert r.status_code == 422
    org = await org_of(client, tenant)
    row = await org_creds._row(db, org, "twilio_voice")
    assert TOKEN not in row.data_enc
    assert (await org_creds.effective(db, org, "twilio_voice"))["mode"] == "test"


@pytest.mark.asyncio
async def test_wrong_creds_rejected_and_not_stored(client, tenant, fake_twilio, db):
    r = await client.put("/api/telephony/twilio", json={"account_sid": SID, "auth_token": "nope"}, headers=tenant)
    assert r.status_code == 422
    r = await client.put("/api/telephony/twilio", json={"account_sid": "bad", "auth_token": "x"}, headers=tenant)
    assert r.status_code == 422
    assert (await client.get("/api/telephony/config", headers=tenant)).json()["twilio"]["connected"] is False


@pytest.mark.asyncio
async def test_live_keys_refused_unless_production_and_allowed(client, tenant, monkeypatch):
    fake = FakeTwilio(live=True)
    monkeypatch.setattr(twilio_voice, "transport", httpx.MockTransport(fake))
    r = await client.put("/api/telephony/twilio", json={"account_sid": SID, "auth_token": TOKEN}, headers=tenant)
    assert r.status_code == 403 and "live" in r.json()["detail"].lower()
    assert (await client.get("/api/telephony/config", headers=tenant)).json()["twilio"]["connected"] is False
    # the flag alone isn't enough outside production
    monkeypatch.setattr(settings, "ALLOW_LIVE_TELEPHONY", True)
    assert (await client.put("/api/telephony/twilio", json={"account_sid": SID, "auth_token": TOKEN},
                             headers=tenant)).status_code == 403
    # production without the flag: refused
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "ALLOW_LIVE_TELEPHONY", False)
    assert twilio_voice.live_allowed() is False
    monkeypatch.setattr(settings, "ALLOW_LIVE_TELEPHONY", True)
    assert twilio_voice.live_allowed() is True


@pytest.mark.asyncio
async def test_click_to_call_refused_when_stored_keys_are_live(client, tenant, fake_twilio, db):
    await connect(client, tenant)
    org = await org_of(client, tenant)
    await set_my_phone(client, tenant, db)
    creds = await org_creds.effective(db, org, "twilio_voice")
    await org_creds.set_creds(db, org, "twilio_voice", {**creds, "mode": "live"})
    fake_twilio.calls.clear()
    r = await client.post("/api/telephony/call", json={"to_number": "+14155550199"}, headers=tenant)
    assert r.status_code == 403
    assert fake_twilio.calls == []
    # and Twilio's webhooks for that shop are refused too
    assert (await post_signed(client, f"/api/comms/twilio/voice?org_id={org}", inbound())).status_code == 403


@pytest.mark.asyncio
async def test_test_connection(client, tenant, fake_twilio):
    assert (await client.post("/api/telephony/twilio/test", headers=tenant)).status_code == 409
    await connect(client, tenant, enable=False)
    r = await client.post("/api/telephony/twilio/test", headers=tenant)
    assert r.status_code == 200 and r.json() == {"ok": True, "mode": "test", "account_name": None}


@pytest.mark.asyncio
async def test_settings_endpoints_need_manage_settings(client, tenant, fake_twilio):
    h = await limited(client, tenant, "lim-tel@test.dev")
    for method, path, body in [
        ("put", "/api/telephony/twilio", {"account_sid": SID, "auth_token": TOKEN}),
        ("post", "/api/telephony/twilio/test", None),
        ("delete", "/api/telephony/twilio", None),
        ("put", "/api/telephony/config", {"provider": "simulated", "enabled": True}),
        ("put", "/api/telephony/automation/contacts", {"enabled": True}),
        ("post", "/api/telephony/simulate/inbound", {"from_number": "+14155550100"}),
    ]:
        kw = {"json": body} if body is not None else {}
        r = await getattr(client, method)(path, headers=h, **kw)
        assert r.status_code == 403, (method, path, r.text)


# ── D1: click-to-call through Twilio ─────────────────────────────────────────
@pytest.mark.asyncio
async def test_click_to_call_rings_employee_then_dials_customer(client, tenant, fake_twilio, db):
    await connect(client, tenant)
    org = await org_of(client, tenant)
    # Twilio rings the employee first, so they need a phone on their profile
    r = await client.post("/api/telephony/call", json={"to_number": "+14155550199"}, headers=tenant)
    assert r.status_code == 409 and "profile" in r.json()["detail"]
    await set_my_phone(client, tenant, db)

    r = await client.post("/api/telephony/call", json={"to_number": "+14155550199"}, headers=tenant)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["provider"] == "twilio" and body["status"] == "ringing" and body["test_mode"] is True
    sent = fake_twilio.calls[-1]
    assert sent["To"] == ["+14155550111"] and sent["From"] == [NUMBER]
    assert f"call_id={body['id']}" in sent["Url"][0] and "/api/comms/twilio/voice" in sent["Url"][0]
    assert "/api/comms/twilio/status" in sent["StatusCallback"][0]
    call = (await db.execute(select(Call).where(Call.id == body["id"]))).scalar_one()
    assert call.provider_sid == body["provider_sid"] and call.direction == "outbound"

    # the employee picks up: our voice webhook dials the customer
    path = f"/api/comms/twilio/voice?org_id={org}&call_id={call.id}"
    form = {"CallSid": call.provider_sid, "AccountSid": SID, "From": NUMBER, "To": "+14155550111",
            "CallStatus": "in-progress", "Direction": "outbound-api"}
    r = await post_signed(client, path, form)
    assert r.status_code == 200 and "<Dial" in r.text and "+14155550199</Number>" in r.text
    # status callbacks: answered, then the Dial result, then completion
    spath = f"/api/comms/twilio/status?org_id={org}&call_id={call.id}"
    await post_signed(client, spath, {**form, "CallStatus": "in-progress"})
    await post_signed(client, spath, {**form, "DialCallStatus": "completed", "DialCallDuration": "42"})
    await post_signed(client, spath, {**form, "CallStatus": "completed", "CallDuration": "50"})
    await db.refresh(call)
    assert call.status == "completed" and call.duration_s == 42


@pytest.mark.asyncio
async def test_simulated_provider_still_works(client, tenant):
    await client.put("/api/telephony/config", json={"provider": "simulated", "enabled": True,
                                                    "caller_id_number": "+15550000000"}, headers=tenant)
    r = await client.post("/api/telephony/call", json={"to_number": "+15551234567"}, headers=tenant)
    assert r.status_code == 201 and r.json()["status"] == "completed"


@pytest.mark.asyncio
async def test_click_to_call_other_orgs_client_is_404(client, tenant):
    other = await second_tenant(client)
    cid = (await client.post("/api/clients", json={"name": "B", "phone": "+15551112222"}, headers=other)).json()["id"]
    await client.put("/api/telephony/config", json={"provider": "simulated", "enabled": True}, headers=tenant)
    r = await client.post("/api/telephony/call", json={"to_number": "+15551112222", "client_id": cid}, headers=tenant)
    assert r.status_code == 404


# ── D1: webhooks — signature, tenant, idempotency ────────────────────────────
@pytest.mark.asyncio
async def test_voice_webhook_signature_valid_invalid_missing(client, tenant, fake_twilio, db):
    await connect(client, tenant)
    org = await org_of(client, tenant)
    path = f"/api/comms/twilio/voice?org_id={org}"
    form = inbound()
    assert (await client.post(path, data=form)).status_code == 403                       # missing
    bad = {"X-Twilio-Signature": sign("wrong-token", "http://test" + path, form)}
    assert (await client.post(path, data=form, headers=bad)).status_code == 403          # invalid
    tampered = {"X-Twilio-Signature": sign(TOKEN, "http://test" + path, form)}
    assert (await client.post(path, data={**form, "From": "+19999999999"}, headers=tampered)).status_code == 403
    r = await post_signed(client, path, form)                                             # valid
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/xml")
    assert (await db.execute(select(Call).where(Call.org_id == org))).scalars().all()


@pytest.mark.asyncio
async def test_status_webhook_requires_signature(client, tenant, fake_twilio):
    await connect(client, tenant)
    org = await org_of(client, tenant)
    path = f"/api/comms/twilio/status?org_id={org}"
    assert (await client.post(path, data=inbound(CallStatus="completed"))).status_code == 403
    assert (await post_signed(client, path, inbound(CallStatus="completed"), token="x")).status_code == 403


@pytest.mark.asyncio
async def test_voice_webhook_is_idempotent_on_callsid(client, tenant, fake_twilio, db):
    await connect(client, tenant, forward="+14155550123")
    await enable_contacts(client, tenant)
    org = await org_of(client, tenant)
    path = f"/api/comms/twilio/voice?org_id={org}"
    for _ in range(3):
        r = await post_signed(client, path, inbound())
        assert r.status_code == 200 and "+14155550123</Number>" in r.text
    calls = (await db.execute(select(Call).where(Call.org_id == org))).scalars().all()
    clients = (await db.execute(select(Client).where(Client.org_id == org))).scalars().all()
    assert len(calls) == 1 and calls[0].provider_sid == "CA" + "1" * 32
    assert len(clients) == 1


@pytest.mark.asyncio
async def test_tenant_comes_from_registered_account_not_payload(client, tenant, fake_twilio, db):
    """Shop B can't post into shop A: A's URL needs A's token, and A's signature over a
    payload naming B's account (or a number A doesn't own) is refused."""
    other = await second_tenant(client)
    await connect(client, tenant)
    await connect(client, other, sid=SID_B, token=TOKEN_B)
    org_a, org_b = await org_of(client, tenant), await org_of(client, other)
    path_a = f"/api/comms/twilio/voice?org_id={org_a}"
    # B's token on A's URL
    assert (await post_signed(client, path_a, inbound(account=SID_B), token=TOKEN_B)).status_code == 403
    # A's token, but the payload claims B's account
    assert (await post_signed(client, path_a, inbound(account=SID_B))).status_code == 403
    # A's token, a number A never registered
    assert (await post_signed(client, path_a, inbound(to="+14155559999"))).status_code == 403
    # unknown org / org without Twilio
    assert (await post_signed(client, "/api/comms/twilio/voice?org_id=nope", inbound())).status_code == 404
    assert not (await db.execute(select(Call))).scalars().all()
    # the real thing lands in A only
    assert (await post_signed(client, path_a, inbound())).status_code == 200
    assert len((await db.execute(select(Call).where(Call.org_id == org_a))).scalars().all()) == 1
    assert not (await db.execute(select(Call).where(Call.org_id == org_b))).scalars().all()


@pytest.mark.asyncio
async def test_status_callback_cannot_touch_another_orgs_call(client, tenant, fake_twilio, db):
    other = await second_tenant(client)
    await connect(client, tenant)
    await connect(client, other, sid=SID_B, token=TOKEN_B, forward="+14155550123")
    org_a, org_b = await org_of(client, tenant), await org_of(client, other)
    await post_signed(client, f"/api/comms/twilio/voice?org_id={org_b}", inbound(account=SID_B), token=TOKEN_B)
    call_b = (await db.execute(select(Call).where(Call.org_id == org_b))).scalar_one()
    # A signs a status update for B's CallSid on A's URL: no-op for B
    r = await post_signed(client, f"/api/comms/twilio/status?org_id={org_a}&call_id={call_b.id}",
                          inbound(CallStatus="completed"))
    assert r.status_code == 200
    await db.refresh(call_b)
    assert call_b.status == "ringing"
    # A's voice webhook with B's call id: 404
    r = await post_signed(client, f"/api/comms/twilio/voice?org_id={org_a}&call_id={call_b.id}",
                          {**inbound(sid="CA" + "9" * 32), "Direction": "outbound-api"})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_missed_inbound_raises_one_task_on_replays(client, tenant, fake_twilio, db):
    await connect(client, tenant, forward="+14155550123")
    org = await org_of(client, tenant)
    await client.put("/api/comms/automations/task_on_missed_call", json={"enabled": True}, headers=tenant)
    await post_signed(client, f"/api/comms/twilio/voice?org_id={org}", inbound())
    call = (await db.execute(select(Call).where(Call.org_id == org))).scalar_one()
    spath = f"/api/comms/twilio/status?org_id={org}&call_id={call.id}"
    for _ in range(2):
        await post_signed(client, spath, inbound(DialCallStatus="no-answer"))
        await post_signed(client, spath, inbound(CallStatus="completed", CallDuration="20"))
    await db.refresh(call)
    assert call.status == "missed"
    tasks = (await db.execute(select(Task).where(Task.org_id == org))).scalars().all()
    assert len(tasks) == 1


@pytest.mark.asyncio
async def test_no_forward_number_says_we_will_call_back(client, tenant, fake_twilio, db):
    await connect(client, tenant)
    org = await org_of(client, tenant)
    r = await post_signed(client, f"/api/comms/twilio/voice?org_id={org}", inbound())
    assert "<Say>" in r.text and "<Dial" not in r.text
    assert (await db.execute(select(Call).where(Call.org_id == org))).scalar_one().status == "missed"


# ── D2: contacts from calls ──────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_unknown_caller_becomes_tagged_contact_with_manager(client, tenant, fake_twilio, db):
    await connect(client, tenant)
    org = await org_of(client, tenant)
    tag = (await client.post("/api/tags", json={"name": "Caller"}, headers=tenant)).json()
    await enable_contacts(client, tenant, tag_ids=[tag["id"]])
    await post_signed(client, f"/api/comms/twilio/voice?org_id={org}", inbound(frm="+14155550100"))
    c = (await db.execute(select(Client).where(Client.org_id == org))).scalar_one()
    assert c.name == "+1 (415) 555-0100" and c.source == "call" and c.phone == "+14155550100"
    tags = (await db.execute(select(Taggable).where(Taggable.entity_id == c.id))).scalars().all()
    assert [t.tag_id for t in tags] == [tag["id"]]
    # the contact shows where it came from
    detail = (await client.get(f"/api/clients/{c.id}", headers=tenant)).json()
    assert detail["source"] == "call"


@pytest.mark.asyncio
async def test_existing_contact_matched_by_normalised_phone(client, tenant):
    await client.put("/api/telephony/config", json={"provider": "simulated", "enabled": True}, headers=tenant)
    await enable_contacts(client, tenant)
    cid = (await client.post("/api/clients", json={"name": "Dana", "phone": "(415) 555-0100"}, headers=tenant)).json()["id"]
    for num in ("+1 415 555 0100", "+14155550100", "4155550100"):
        r = await client.post("/api/telephony/simulate/inbound", json={"from_number": num}, headers=tenant)
        assert r.status_code == 201 and r.json()["client_id"] == cid and r.json()["client_created"] is False
    clients = (await client.get("/api/clients", headers=tenant)).json()
    assert clients["total"] == 1


@pytest.mark.asyncio
async def test_repeat_unknown_caller_never_duplicates(client, tenant):
    await client.put("/api/telephony/config", json={"provider": "simulated", "enabled": True}, headers=tenant)
    await enable_contacts(client, tenant)
    first = (await client.post("/api/telephony/simulate/inbound", json={"from_number": "+447700900123"}, headers=tenant)).json()
    again = (await client.post("/api/telephony/simulate/inbound", json={"from_number": "07700 900123"}, headers=tenant)).json()
    assert first["client_created"] is True and again["client_id"] == first["client_id"]
    assert (await client.get("/api/clients", headers=tenant)).json()["total"] == 1


@pytest.mark.asyncio
async def test_automation_off_or_direction_incoming_only(client, tenant):
    await client.put("/api/telephony/config", json={"provider": "simulated", "enabled": True}, headers=tenant)
    r = (await client.post("/api/telephony/simulate/inbound", json={"from_number": "+14155550100"}, headers=tenant)).json()
    assert r["client_id"] is None                       # off by default
    await enable_contacts(client, tenant, direction="incoming")
    r = (await client.post("/api/telephony/call", json={"to_number": "+14155550177"}, headers=tenant)).json()
    assert r["client_id"] is None                       # outbound ignored on "incoming"
    await enable_contacts(client, tenant, direction="all")
    r = (await client.post("/api/telephony/call", json={"to_number": "+14155550177"}, headers=tenant)).json()
    assert r["client_created"] is True


@pytest.mark.asyncio
async def test_manager_is_caller_for_outbound_else_extension_owner(client, tenant, db):
    await client.put("/api/telephony/config", json={"provider": "simulated", "enabled": True}, headers=tenant)
    await enable_contacts(client, tenant, direction="all")
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    out = (await client.post("/api/telephony/call", json={"to_number": "+14155550177"}, headers=tenant)).json()
    assert (await db.get(Client, out["client_id"])).manager_id == me["id"]

    emp = await client.post("/api/staff/employees", json={
        "name": "Ext Owner", "email": "ext-owner@test.dev", "password": "pw-ext-12345"}, headers=tenant)
    from backend.models import Employee
    e = await db.get(Employee, emp.json()["id"])
    e.extension = "204"
    await db.commit()
    inb = (await client.post("/api/telephony/simulate/inbound", json={
        "from_number": "+14155550188", "extension": "204"}, headers=tenant)).json()
    assert (await db.get(Client, inb["client_id"])).manager_id == e.id
    # assign_manager off → no manager
    await enable_contacts(client, tenant, assign_manager=False)
    inb = (await client.post("/api/telephony/simulate/inbound", json={
        "from_number": "+14155550189", "extension": "204"}, headers=tenant)).json()
    assert (await db.get(Client, inb["client_id"])).manager_id is None


@pytest.mark.asyncio
async def test_generic_call_webhook_also_creates_contacts(client, tenant, db):
    from backend.models import Organisation
    org = await org_of(client, tenant)
    await enable_contacts(client, tenant)
    secret = (await db.get(Organisation, org)).webhook_secret
    r = await client.post(f"/api/comms/calls/webhook?org_id={org}", json={
        "provider": "aircall", "direction": "inbound", "from_number": "+14155550166", "status": "missed"},
        headers={"X-Webhook-Token": secret} if secret else {})
    assert r.status_code == 201 and r.json()["client_created"] is True


@pytest.mark.asyncio
async def test_automation_rejects_other_orgs_tags(client, tenant):
    other = await second_tenant(client)
    tag_b = (await client.post("/api/tags", json={"name": "B tag"}, headers=other)).json()
    r = await client.put("/api/telephony/automation/contacts", json={
        "enabled": True, "tag_ids": [tag_b["id"]]}, headers=tenant)
    assert r.status_code == 404
    assert (await client.get("/api/telephony/automation/contacts", headers=tenant)).json()["enabled"] is False


@pytest.mark.asyncio
async def test_simulate_refused_on_a_real_provider(client, tenant, fake_twilio):
    await connect(client, tenant)
    r = await client.post("/api/telephony/simulate/inbound", json={"from_number": "+14155550100"}, headers=tenant)
    assert r.status_code == 409


# ── D3: tag filter ───────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_clients_tag_filter(client, tenant):
    t = (await client.post("/api/tags", json={"name": "VIP"}, headers=tenant)).json()
    a = (await client.post("/api/clients", json={"name": "A", "phone": "1"}, headers=tenant)).json()["id"]
    await client.post("/api/clients", json={"name": "B", "phone": "2"}, headers=tenant)
    await client.post("/api/tags/attach", json={"tag_id": t["id"], "entity_type": "contact", "entity_id": a}, headers=tenant)
    r = (await client.get(f"/api/clients?tag={t['id']}", headers=tenant)).json()
    assert r["total"] == 1 and [c["id"] for c in r["items"]] == [a]
    r = (await client.get(f"/api/clients?tag={t['id']}&search=B", headers=tenant)).json()
    assert r["total"] == 0
    assert (await client.get("/api/clients", headers=tenant)).json()["total"] == 2


@pytest.mark.asyncio
async def test_clients_tag_filter_other_orgs_tag_is_404(client, tenant):
    other = await second_tenant(client)
    tb = (await client.post("/api/tags", json={"name": "Theirs"}, headers=other)).json()
    assert (await client.get(f"/api/clients?tag={tb['id']}", headers=tenant)).status_code == 404
    assert (await client.get("/api/clients?tag=missing", headers=tenant)).status_code == 404


# ── Real Twilio test API (opt-in) ────────────────────────────────────────────
@pytest.mark.skipif(not (os.environ.get("TWILIO_TEST_ACCOUNT_SID") and os.environ.get("TWILIO_TEST_AUTH_TOKEN")),
                    reason="set TWILIO_TEST_ACCOUNT_SID / TWILIO_TEST_AUTH_TOKEN to run against Twilio's test API")
@pytest.mark.asyncio
async def test_smoke_real_twilio_test_credentials():
    sid, tok = os.environ["TWILIO_TEST_ACCOUNT_SID"], os.environ["TWILIO_TEST_AUTH_TOKEN"]
    assert (await twilio_voice.probe(sid, tok))["mode"] == "test"
    res = await twilio_voice.create_call(sid, tok, to="+14108675310", from_=twilio_voice.TEST_FROM_NUMBER,
                                         url="http://demo.twilio.com/docs/voice.xml",
                                         status_callback="https://example.com/status")
    assert res["sid"].startswith("CA")


# ── Review fixes ─────────────────────────────────────────────────────────────
def test_number_matching_rules():
    from backend.services.call_contacts import same_number
    assert same_number("+49 30 12 34 56", "+493012 3456")
    assert same_number("(415) 555-0100", "+1 415 555 0100")
    assert not same_number("+91 98765 43210", "+1 987 654 3210")   # different country codes


@pytest.mark.asyncio
async def test_match_survives_formatting_that_splits_the_last_digits(client, tenant):
    await client.put("/api/telephony/config", json={"provider": "simulated", "enabled": True}, headers=tenant)
    await enable_contacts(client, tenant)
    cid = (await client.post("/api/clients", json={"name": "Jonas", "phone": "+49 30 12 34 56"}, headers=tenant)).json()["id"]
    r = (await client.post("/api/telephony/simulate/inbound", json={"from_number": "+4930123456"}, headers=tenant)).json()
    assert r["client_id"] == cid and r["client_created"] is False


@pytest.mark.asyncio
async def test_status_callbacks_still_land_after_switching_provider(client, tenant, fake_twilio, db):
    await connect(client, tenant, forward="+14155550123")
    org = await org_of(client, tenant)
    await post_signed(client, f"/api/comms/twilio/voice?org_id={org}", inbound())
    call = (await db.execute(select(Call).where(Call.org_id == org))).scalar_one()
    await client.put("/api/telephony/config", json={"provider": "simulated", "enabled": True}, headers=tenant)
    # new calls are refused, but the in-flight call still gets its outcome
    assert (await post_signed(client, f"/api/comms/twilio/voice?org_id={org}", inbound(sid="CA" + "2" * 32))).status_code == 404
    r = await post_signed(client, f"/api/comms/twilio/status?org_id={org}&call_id={call.id}", inbound(DialCallStatus="completed"))
    assert r.status_code == 200
    await db.refresh(call)
    assert call.status == "completed"


@pytest.mark.asyncio
async def test_outbound_completed_before_answered_stays_completed(client, tenant, fake_twilio, db):
    await connect(client, tenant)
    await set_my_phone(client, tenant, db)
    org = await org_of(client, tenant)
    body = (await client.post("/api/telephony/call", json={"to_number": "+14155550199"}, headers=tenant)).json()
    form = {"CallSid": body["provider_sid"], "AccountSid": SID, "From": NUMBER, "To": "+14155550111"}
    await post_signed(client, f"/api/comms/twilio/status?org_id={org}&call_id={body['id']}", {**form, "CallStatus": "completed"})
    call = await db.get(Call, body["id"])
    assert call.status == "completed"


@pytest.mark.asyncio
async def test_saving_config_keeps_other_provider_keys(client, tenant, db):
    from backend.models import TelephonyConfig
    await client.put("/api/telephony/config", json={"provider": "aircall", "enabled": True,
                                                    "config": {"api_id": "x1"}}, headers=tenant)
    org = await org_of(client, tenant)
    cfg = (await db.execute(select(TelephonyConfig).where(TelephonyConfig.org_id == org))).scalar_one()
    cfg.config = {"api_id": "x1"}
    await db.commit()
    await client.put("/api/telephony/config", json={"provider": "aircall", "enabled": True, "forward_to": "+14155550123"}, headers=tenant)
    await db.refresh(cfg)
    assert cfg.config == {"api_id": "x1", "forward_to": "+14155550123"}


def test_short_national_numbers_match_their_international_form():
    from backend.services.call_contacts import same_number
    assert same_number("9123 4567", "+65 9123 4567")
    assert not same_number("9123 4567", "+65 9123 4568")
    assert not same_number("123 4567", "+1 415 123 4567")   # 7 digits can't be a 10-digit tail plus ≤3 cc


def test_signature_sorts_repeated_values_like_twilio():
    a = twilio_voice.signature("t", "https://x/y", [("E", "b"), ("E", "a"), ("K", "v")])
    b = twilio_voice.signature("t", "https://x/y", [("K", "v"), ("E", "a"), ("E", "b")])
    assert a == b


@pytest.mark.asyncio
async def test_main_line_doesnt_pick_an_extension_by_suffix(client, tenant, db):
    from backend.models import Employee
    from backend.services.call_contacts import extension_owner
    org = await org_of(client, tenant)
    emp = await client.post("/api/staff/employees", json={
        "name": "Ext 100", "email": "ext100@test.dev", "password": "pw-ext-12345"}, headers=tenant)
    e = await db.get(Employee, emp.json()["id"])
    e.extension = "100"
    await db.commit()
    assert await extension_owner(db, org, extension=None, to_number="+14155550100") is None
    assert (await extension_owner(db, org, extension=None, to_number="100")).id == e.id


@pytest.mark.asyncio
async def test_rejected_twilio_call_creates_no_contact(client, tenant, fake_twilio, db, monkeypatch):
    await connect(client, tenant)
    await set_my_phone(client, tenant, db)
    await enable_contacts(client, tenant, direction="all")

    def refuse(request):
        return httpx.Response(400, json={"code": 21211, "message": "Invalid 'To' Phone Number"})
    monkeypatch.setattr(twilio_voice, "transport", httpx.MockTransport(refuse))
    r = await client.post("/api/telephony/call", json={"to_number": "+14155550199"}, headers=tenant)
    assert r.status_code == 502 and "21211" in r.json()["detail"]
    org = await org_of(client, tenant)
    assert not (await db.execute(select(Client).where(Client.org_id == org))).scalars().all()
