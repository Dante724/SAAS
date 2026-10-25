"""Webhook hardening (findings B1-B16): one regression test per finding, often several.

DNS is stubbed and every HTTP call goes through httpx.MockTransport, so nothing touches the network.
"""
import asyncio
import json
import socket
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select

from backend.config import settings
from backend.core import ssrf
from backend.models import (
    AutomationRule,
    Call,
    Employee,
    Organisation,
    StaffNotification,
    WebhookDelivery,
    WebhookEndpoint,
)
from backend.services import webhooks as wh
from backend.services.webhook_verify_example import TEST_VECTOR, compute_signature, verify

PUBLIC = "93.184.216.34"


# ── fixtures ──────────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def dns(monkeypatch):
    """Hostname -> IPs. A list value may be a callable for answers that change (rebinding)."""
    table = {"hooks.example.com": [PUBLIC], "erp.example.com": [PUBLIC],
             "alibaba.example.com": ["100.100.100.200"], "cgnat.example.com": ["100.64.1.2"]}

    def fake(host, port, *a, **kw):
        ips = table.get(host) or [host]
        if callable(ips):
            ips = ips()
        return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))
                for ip in ips]
    monkeypatch.setattr(socket, "getaddrinfo", fake)
    return table


@pytest.fixture
def transport(monkeypatch):
    """Route every httpx.AsyncClient through a recording mock transport."""
    state = SimpleNamespace(seen=[], responses=[], handler=None)

    async def handler(request):
        state.seen.append(request)
        if state.handler:
            return await state.handler(request)
        return state.responses.pop(0) if state.responses else httpx.Response(200)

    real = httpx.AsyncClient

    class Recording(real):
        def __init__(self, *a, **kw):
            kw["transport"] = httpx.MockTransport(handler)
            super().__init__(*a, **kw)
    monkeypatch.setattr(httpx, "AsyncClient", Recording)
    return state


async def _org_id(client, h):
    return (await client.get("/api/auth/me", headers=h)).json()["org_id"]


async def _endpoint(db, org_id, events="*", url="https://hooks.example.com/in", secret="whsec_s1", **kw):
    ep = WebhookEndpoint(org_id=org_id, url=url, secret=wh.store_secret(secret), events=events, active=True, **kw)
    db.add(ep)
    await db.commit()
    return ep


async def _deliveries(db, org_id, event=None):
    q = select(WebhookDelivery).where(WebhookDelivery.org_id == org_id)
    if event:
        q = q.where(WebhookDelivery.event == event)
    db.expire_all()
    return (await db.execute(q.order_by(WebhookDelivery.created_at))).scalars().all()


def _delivery(**kw):
    base = dict(id="dlv_1", attempts=0, status="pending", error=None, response_code=None,
                delivered_at=None, next_attempt_at=None)
    base.update(kw)
    return SimpleNamespace(**base)


# ── B1: signing scheme ───────────────────────────────────────────────────────────────────────

def test_b1_test_vector_for_the_docs():
    v = TEST_VECTOR
    assert compute_signature(v["secret"], v["timestamp"], v["body"]) == v["signature"]
    header = f"t={v['timestamp']},v1={v['signature']}"
    assert verify(v["secret"], header, v["body"], now=v["timestamp"] + 10)
    # the production signer is the same function
    assert wh.signature_header([v["secret"]], v["timestamp"], v["body"]) == header


def test_b1_replay_window_and_tampering():
    v = TEST_VECTOR
    header = f"t={v['timestamp']},v1={v['signature']}"
    assert verify(v["secret"], header, v["body"], now=v["timestamp"] + 299)
    assert not verify(v["secret"], header, v["body"], now=v["timestamp"] + 301)       # too old
    assert not verify(v["secret"], header, v["body"], now=v["timestamp"] - 301)       # from the future
    assert not verify(v["secret"], header, v["body"] + " ", now=v["timestamp"])       # body changed
    assert not verify("whsec_other", header, v["body"], now=v["timestamp"])
    moved = f"t={v['timestamp'] + 60},v1={v['signature']}"                            # t can't be swapped
    assert not verify(v["secret"], moved, v["body"], now=v["timestamp"] + 60)
    assert not verify(v["secret"], "garbage", v["body"])


@pytest.mark.asyncio
async def test_b1_headers_and_same_webhook_id_on_retry(dns, transport):
    body = wh.envelope("evt_1", "order.created", "org_1", {"id": "o1"}, datetime.utcnow())
    d = _delivery(id="dlv_fixed")
    transport.responses.append(httpx.Response(500))
    await wh._attempt(d, "https://hooks.example.com/in", ["whsec_s1"], body)
    await wh._attempt(d, "https://hooks.example.com/in", ["whsec_s1"], body)
    first, second = transport.seen
    for req in (first, second):
        assert req.headers["tekly-webhook-id"] == "dlv_fixed"
        assert req.headers["tekly-event"] == "order.created"
        sig = req.headers["tekly-signature"]
        assert sig.startswith("t=") and ",v1=" in sig
        assert verify("whsec_s1", sig, req.content)
        # legacy headers kept for one cycle
        assert req.headers["x-tekly-signature"].startswith("sha256=")
        assert req.headers["x-repairos-signature"] == req.headers["x-tekly-signature"]
        assert req.headers["x-repairos-event"] == "order.created"
        assert "X-Tekly-Signature" in req.headers["tekly-deprecated-headers"]


@pytest.mark.asyncio
async def test_b1_envelope_shape(client, tenant, db):
    org = await _org_id(client, tenant)
    await _endpoint(db, org, "order.created")
    r = await client.post("/api/orders", json={"complaint": "Screen"}, headers=tenant)
    assert r.status_code in (200, 201), r.text
    (d,) = await _deliveries(db, org, "order.created")
    body = json.loads(d.payload)
    assert body["id"].startswith("evt_") and body["event"] == "order.created"
    assert body["api_version"] == wh.API_VERSION and body["org_id"] == org
    assert body["created_at"].endswith("Z") and body["data"]["id"] == r.json()["id"]


# ── B2 / B11: automation webhooks go through the signed, pinned outbox ──────────────────────

@pytest.mark.asyncio
async def test_b2_automation_rule_rejects_internal_url_at_save(client, tenant, dns):
    for bad in ("http://169.254.169.254/latest", "https://alibaba.example.com/x", "http://127.0.0.1:6379/"):
        r = await client.post("/api/automations", headers=tenant, json={
            "name": "hook", "event": "order_created", "action_type": "webhook", "action_config": {"url": bad}})
        assert r.status_code == 400, (bad, r.text)
        assert "public" in r.json()["detail"] and "100.100" not in r.json()["detail"]


@pytest.mark.asyncio
async def test_b2_automation_webhook_is_queued_signed_not_sent_inline(client, tenant, db, dns, monkeypatch):
    class NoInline(httpx.AsyncClient):
        def __init__(self, *a, **k):
            raise AssertionError("automation webhook was sent inline")
    org = await _org_id(client, tenant)
    r = await client.post("/api/automations", headers=tenant, json={
        "name": "To ERP", "event": "order_created", "action_type": "webhook",
        "action_config": {"url": "https://erp.example.com/hook"}})
    assert r.status_code == 201, r.text
    assert r.json()["signing_secret"].startswith("whsec_")          # shown once
    rule_id = r.json()["id"]
    monkeypatch.setattr(httpx, "AsyncClient", NoInline)
    monkeypatch.setattr(wh, "deliver_pending", _noop_async)          # keep the row pending
    o = await client.post("/api/orders", json={"complaint": "x"}, headers=tenant)
    assert o.status_code in (200, 201), o.text
    ep = (await db.execute(select(WebhookEndpoint).where(
        WebhookEndpoint.org_id == org, WebhookEndpoint.kind == "automation"))).scalar_one()
    assert ep.source_id == rule_id and ep.url == "https://erp.example.com/hook"
    ep_id = ep.id
    rows = await _deliveries(db, org, "order_created")
    assert len(rows) == 1 and rows[0].endpoint_id == ep_id
    body = json.loads(rows[0].payload)
    assert body["data"]["rule"] == "To ERP" and body["order_id"] == o.json()["id"]
    # B11: the feed line is generic
    rule = (await db.execute(select(AutomationRule).where(AutomationRule.id == rule_id))).scalar_one()
    assert rule.fire_count == 1
    # hidden endpoints are not listed, capped or fanned out to
    assert (await client.get("/api/webhooks", headers=tenant)).json()["items"] == []


async def _noop_async(*a, **k):
    return 0


@pytest.mark.asyncio
async def test_b11_automation_summary_is_generic(db, client, tenant, dns):
    from backend.services import automation as auto
    org = await _org_id(client, tenant)
    rule = AutomationRule(org_id=org, name="r", event="order_created", action_type="webhook",
                          action_config=json.dumps({"url": "https://erp.example.com/h"}), active=True)
    db.add(rule)
    await db.commit()
    summary = await auto._do_action(db, org, rule, SimpleNamespace(id="o", order_number="1", status="new"), None)
    assert summary == "webhook queued"
    await db.rollback()


# ── B3: SSRF guard ───────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("ip, public", [
    ("100.100.100.200", False), ("100.64.0.1", False), ("192.0.0.8", False), ("198.18.0.1", False),
    ("224.0.0.1", False), ("2001:db8::1", False), ("2002:a9fe:a9fe::", False),
    (PUBLIC, True), ("2606:2800:220:1::1", True),
])
def test_b3_only_global_unicast_is_public(ip, public):
    assert ssrf._ip_is_public(ip) is public


def test_b3_alibaba_metadata_host_blocked(dns):
    with pytest.raises(ssrf.UnsafeURLError):
        ssrf.assert_webhook_url("https://alibaba.example.com/latest/meta-data")
    with pytest.raises(ssrf.UnsafeURLError):
        ssrf.pinned_target("https://cgnat.example.com/", webhook=True)


def test_b3_https_and_ports_when_deployed(dns, monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    for bad in ("http://hooks.example.com/in", "https://hooks.example.com:8080/in",
                "https://user:pw@hooks.example.com/in"):
        with pytest.raises(ssrf.UnsafeURLError):
            ssrf.assert_webhook_url(bad)
    assert ssrf.assert_webhook_url("https://hooks.example.com/in")
    assert ssrf.assert_webhook_url("https://hooks.example.com:8443/in")
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    assert ssrf.assert_webhook_url("http://hooks.example.com/in")      # http on 80 is fine locally
    with pytest.raises(ssrf.UnsafeURLError):
        ssrf.assert_webhook_url("http://hooks.example.com:3000/in")


@pytest.mark.asyncio
async def test_b3_http_endpoint_refused_by_api_when_deployed(client, tenant, dns, monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "staging")
    r = await client.post("/api/webhooks", headers=tenant, json={"url": "http://hooks.example.com/in"})
    assert r.status_code == 400 and r.json()["detail"] == "URL must be a public https:// address"


@pytest.mark.asyncio
async def test_b3_rebinding_connects_to_the_checked_ip(dns, transport):
    """The resolver answers public first and internal after: the request still goes to the
    address that passed the check, never a second lookup."""
    answers = iter([[PUBLIC], ["100.100.100.200"], ["127.0.0.1"]])
    dns["hooks.example.com"] = lambda: next(answers)
    d = _delivery()
    await wh._attempt(d, "https://hooks.example.com/in", "whsec_s", '{"event": "x"}')
    assert d.status == "sent"
    assert [r.url.host for r in transport.seen] == [PUBLIC]
    assert transport.seen[0].headers["host"] == "hooks.example.com"


@pytest.mark.asyncio
async def test_b3_rebinding_to_metadata_is_blocked_on_delivery(dns, transport):
    dns["hooks.example.com"] = ["100.100.100.200"]
    d = _delivery()
    await wh._attempt(d, "https://hooks.example.com/in", "whsec_s", '{"event": "x"}')
    assert d.status == "failed" and d.error.startswith("Blocked") and transport.seen == []
    assert "100.100" not in d.error


# ── B4: deadline, size cap, concurrency ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_b4_total_deadline(dns, transport, monkeypatch):
    monkeypatch.setattr(wh, "DELIVERY_DEADLINE_S", 0.2)

    async def slow(request):
        await asyncio.sleep(5)
        return httpx.Response(200)
    transport.handler = slow
    d = _delivery()
    t0 = asyncio.get_running_loop().time()
    await wh._attempt(d, "https://hooks.example.com/in", "s", '{"event": "x"}')
    assert asyncio.get_running_loop().time() - t0 < 2
    assert d.status == "failed" and d.error == "Timed out"


@pytest.mark.asyncio
async def test_b4_response_body_is_capped(dns, transport):
    pulled = []

    class Huge(httpx.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(30 * 1024):            # 30 MB in 1 KB chunks
                pulled.append(1)
                yield b"x" * 1024

    async def big(request):
        return httpx.Response(200, stream=Huge())
    transport.handler = big
    d = _delivery()
    await wh._attempt(d, "https://hooks.example.com/in", "s", '{"event": "x"}')
    assert d.status == "sent"
    assert len(pulled) <= wh.MAX_RESPONSE_BYTES // 1024 + 1


@pytest.mark.asyncio
async def test_b4_one_in_flight_per_endpoint_and_global_limit(client, tenant, db, dns, transport):
    org = await _org_id(client, tenant)
    ep1 = await _endpoint(db, org)
    ep2 = await _endpoint(db, org, url="https://erp.example.com/in")
    ids = []
    for ep in (ep1, ep1, ep1, ep2, ep2):
        d = WebhookDelivery(org_id=org, endpoint_id=ep.id, event="order.created",
                            payload='{"event": "order.created"}', status="pending", attempts=0)
        db.add(d)
        await db.flush()
        ids.append(d.id)
    await db.commit()
    live = {"hooks.example.com": 0, "erp.example.com": 0}
    peak = {"hooks.example.com": 0, "erp.example.com": 0, "all": 0}

    async def slowish(request):
        host = request.headers["host"]
        live[host] += 1
        peak[host] = max(peak[host], live[host])
        peak["all"] = max(peak["all"], sum(live.values()))
        await asyncio.sleep(0.05)
        live[host] -= 1
        return httpx.Response(204)
    transport.handler = slowish
    results = await wh.deliver_many(ids)
    assert results.count("sent") == 5
    assert peak["hooks.example.com"] == 1 and peak["erp.example.com"] == 1 and peak["all"] == 2
    sem, _ = wh._limits()
    assert sem._value == wh.MAX_CONCURRENT_DELIVERIES == 20


# ── B5: backoff, stuck rows, auto-disable ────────────────────────────────────────────────────

def test_b5_retry_schedule():
    assert [wh.next_attempt_delay(n) for n in range(1, 8)] == [60, 300, 1800, 7200, 28800, 86400, None]
    assert wh.MAX_ATTEMPTS == 7


@pytest.mark.asyncio
async def test_b5_failure_sets_next_attempt(dns, transport):
    now = datetime(2026, 10, 25, 12, 0, 0)
    transport.responses.extend([httpx.Response(500), httpx.Response(502)])
    d = _delivery()
    await wh._attempt(d, "https://hooks.example.com/in", "s", '{"event": "x"}', now=now)
    assert d.status == "failed" and d.next_attempt_at == now + timedelta(minutes=1)
    await wh._attempt(d, "https://hooks.example.com/in", "s", '{"event": "x"}', now=now)
    assert d.next_attempt_at == now + timedelta(minutes=5)
    d.attempts = 6
    transport.responses.append(httpx.Response(500))
    await wh._attempt(d, "https://hooks.example.com/in", "s", '{"event": "x"}', now=now)
    assert d.status == "failed" and d.next_attempt_at is None          # gave up after 7


@pytest.mark.asyncio
async def test_b5_sweep_honours_next_attempt_and_reclaims_stuck(client, tenant, db, dns, transport):
    org = await _org_id(client, tenant)
    ep = await _endpoint(db, org)
    now = datetime.utcnow()
    later = WebhookDelivery(org_id=org, endpoint_id=ep.id, event="e", payload='{"event": "e"}',
                            status="failed", attempts=1, next_attempt_at=now + timedelta(minutes=4))
    due = WebhookDelivery(org_id=org, endpoint_id=ep.id, event="e", payload='{"event": "e"}',
                          status="failed", attempts=1, next_attempt_at=now - timedelta(seconds=1))
    stuck = WebhookDelivery(org_id=org, endpoint_id=ep.id, event="e", payload='{"event": "e"}',
                            status="sending", attempts=1, claimed_at=now - timedelta(minutes=3))
    fresh_claim = WebhookDelivery(org_id=org, endpoint_id=ep.id, event="e", payload='{"event": "e"}',
                                  status="sending", attempts=1, claimed_at=now)
    gave_up = WebhookDelivery(org_id=org, endpoint_id=ep.id, event="e", payload='{"event": "e"}',
                              status="failed", attempts=3, next_attempt_at=None)
    db.add_all([later, due, stuck, fresh_claim, gave_up])
    await db.commit()
    assert await wh.retry_sweep(now=now) == 2
    for row in (later, due, stuck, fresh_claim, gave_up):
        await db.refresh(row)
    assert due.status == "sent" and stuck.status == "sent"
    assert later.status == "failed" and later.attempts == 1
    assert fresh_claim.status == "sending"
    assert gave_up.status == "failed" and gave_up.attempts == 3


@pytest.mark.asyncio
async def test_b5_auto_disable_after_24h_and_notify_admins(client, tenant, db, dns, transport):
    org = await _org_id(client, tenant)
    ep = await _endpoint(db, org, failing_since=datetime.utcnow() - timedelta(hours=25))
    d = WebhookDelivery(org_id=org, endpoint_id=ep.id, event="e", payload='{"event": "e"}', status="pending", attempts=0)
    db.add(d)
    await db.commit()
    transport.responses.append(httpx.Response(503))
    assert await wh.deliver_one(d.id) == "failed"
    await db.refresh(ep)
    assert ep.active is False and ep.disabled_reason == wh.DISABLED_REASON
    owner = (await db.execute(select(Employee).where(Employee.org_id == org))).scalars().first()
    notes = (await db.execute(select(StaffNotification).where(
        StaffNotification.employee_id == owner.id, StaffNotification.kind == "webhook.disabled"))).scalars().all()
    assert len(notes) == 1
    # a later delivery is not attempted
    d2 = WebhookDelivery(org_id=org, endpoint_id=ep.id, event="e", payload='{"event": "e"}', status="pending", attempts=0)
    db.add(d2)
    await db.commit()
    assert await wh.deliver_one(d2.id) == "abandoned"
    # turning it back on clears the failure clock
    r = await client.patch(f"/api/webhooks/{ep.id}", headers=tenant, json={"active": True})
    assert r.status_code == 200 and r.json()["endpoint"]["disabled_reason"] is None


@pytest.mark.asyncio
async def test_b5_a_short_failure_does_not_disable(client, tenant, db, dns, transport):
    org = await _org_id(client, tenant)
    ep = await _endpoint(db, org, failing_since=datetime.utcnow() - timedelta(hours=2))
    d = WebhookDelivery(org_id=org, endpoint_id=ep.id, event="e", payload='{"event": "e"}', status="pending", attempts=0)
    db.add(d)
    await db.commit()
    transport.responses.append(httpx.Response(500))
    await wh.deliver_one(d.id)
    await db.refresh(ep)
    assert ep.active is True


# ── B6: secrets ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_b6_secret_shown_once_and_encrypted_at_rest(client, tenant, db, dns, monkeypatch):
    from cryptography.fernet import Fernet
    monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    r = await client.post("/api/webhooks", headers=tenant, json={"url": "https://hooks.example.com/in"})
    assert r.status_code == 201, r.text
    secret = r.json()["secret"]
    assert secret.startswith("whsec_") and len(secret) > 40
    listed = (await client.get("/api/webhooks", headers=tenant)).json()["items"][0]
    assert "secret" not in listed and listed["secret_hint"] == "whsec_…" + secret[-4:]
    row = (await db.execute(select(WebhookEndpoint).where(WebhookEndpoint.id == r.json()["id"]))).scalar_one()
    assert row.secret.startswith("enc:v1:") and secret not in row.secret
    assert wh.signing_secrets(row) == [secret]


@pytest.mark.asyncio
async def test_b6_rotation_overlap(client, tenant, db, dns, transport):
    r = await client.post("/api/webhooks", headers=tenant, json={"url": "https://hooks.example.com/in"})
    eid, old = r.json()["id"], r.json()["secret"]
    rot = await client.post(f"/api/webhooks/{eid}/rotate", headers=tenant)
    assert rot.status_code == 200
    new = rot.json()["secret"]
    assert new != old and rot.json()["previous_secret_expires_at"]
    row = (await db.execute(select(WebhookEndpoint).where(WebhookEndpoint.id == eid))).scalar_one()
    assert wh.signing_secrets(row) == [new, old]
    body = '{"event": "order.created"}'
    await wh._attempt(_delivery(), row.url, wh.signing_secrets(row), body)
    sig = transport.seen[0].headers["tekly-signature"]
    assert sig.count("v1=") == 2
    assert verify(old, sig, body) and verify(new, sig, body)          # either secret verifies
    after = datetime.utcnow() + timedelta(hours=25)
    assert wh.signing_secrets(row, now=after) == [new]               # overlap over


# ── B7: cap, event validation, URL length ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_b7_cap_of_ten(client, tenant, dns):
    for i in range(10):
        r = await client.post("/api/webhooks", headers=tenant, json={"url": f"https://hooks.example.com/{i}"})
        assert r.status_code == 201, r.text
    r = await client.post("/api/webhooks", headers=tenant, json={"url": "https://hooks.example.com/11"})
    assert r.status_code == 409
    # a deleted endpoint frees a slot
    first = (await client.get("/api/webhooks", headers=tenant)).json()["items"][0]["id"]
    assert (await client.delete(f"/api/webhooks/{first}", headers=tenant)).status_code == 200
    assert (await client.post("/api/webhooks", headers=tenant,
                              json={"url": "https://hooks.example.com/11"})).status_code == 201


@pytest.mark.asyncio
async def test_b7_event_validation_and_url_length(client, tenant, dns):
    bad = await client.post("/api/webhooks", headers=tenant, json={"url": "https://hooks.example.com/x", "events": "order.craeted"})
    assert bad.status_code == 422 and "order.craeted" in bad.json()["detail"]
    mixed = await client.post("/api/webhooks", headers=tenant, json={"url": "https://hooks.example.com/x", "events": ["*", "order.created"]})
    assert mixed.status_code == 422
    ok = await client.post("/api/webhooks", headers=tenant, json={
        "url": "https://hooks.example.com/x", "events": ["order.created", "booking.created", "order.created"]})
    assert ok.status_code == 201 and ok.json()["events"] == "order.created,booking.created"
    long_url = "https://hooks.example.com/" + "a" * 480
    assert (await client.post("/api/webhooks", headers=tenant, json={"url": long_url})).status_code == 422
    eid = ok.json()["id"]
    assert (await client.patch(f"/api/webhooks/{eid}", headers=tenant, json={"events": "nope"})).status_code == 422


# ── B8: inbound token webhooks fail closed ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_b8_null_secret_fails_closed(client, tenant, db):
    org = await _org_id(client, tenant)
    o = await db.get(Organisation, org)
    o.webhook_secret = None
    await db.commit()
    body = {"channel": "sms", "handle": "+1", "body": "x"}
    r = await client.post(f"/api/inbox/webhook/{org}", json=body)
    assert r.status_code == 401
    r = await client.post(f"/api/inbox/webhook/{org}", json=body, headers={"X-Webhook-Token": ""})
    assert r.status_code == 401
    unknown = await client.post("/api/inbox/webhook/not-an-org", json=body, headers={"X-Webhook-Token": "x"})
    assert unknown.status_code == 401 and unknown.json() == r.json()       # the same 401
    call = await client.post(f"/api/comms/calls/webhook?org_id={org}", json={"from_number": "+1"})
    assert call.status_code == 401


@pytest.mark.asyncio
async def test_b8_new_orgs_always_get_a_token(db):
    o = Organisation(name="Fresh")
    db.add(o)
    await db.commit()
    assert o.webhook_secret and len(o.webhook_secret) >= 24


@pytest.mark.asyncio
async def test_b8_rate_limited_per_org(client, tenant):
    org = await _org_id(client, tenant)
    body = {"channel": "sms", "handle": "+1", "body": "x"}
    codes = [(await client.post(f"/api/inbox/webhook/{org}", json=body, headers={"X-Webhook-Token": "bad"})).status_code
             for _ in range(121)]
    assert codes[:120] == [401] * 120 and codes[120] == 429


# ── B9: calls webhook ────────────────────────────────────────────────────────────────────────

async def _token(client, h):
    return (await client.get("/api/settings/webhook-secret", headers=h)).json()["webhook_secret"]


@pytest.mark.asyncio
async def test_b9_call_webhook_is_idempotent(client, tenant, db):
    org = await _org_id(client, tenant)
    h = {"X-Webhook-Token": await _token(client, tenant)}
    ev = {"provider": "aircall", "from_number": "+15550001", "to_number": "+15550002",
          "status": "ringing", "external_id": "call-123"}
    first = await client.post(f"/api/comms/calls/webhook?org_id={org}", json=ev, headers=h)
    assert first.status_code == 201, first.text
    again = await client.post(f"/api/comms/calls/webhook?org_id={org}", headers=h, json={
        **ev, "status": "completed", "duration_s": 42, "recording_url": "https://rec.example.com/1.mp3"})
    assert again.status_code == 200 and again.json()["id"] == first.json()["id"] and again.json()["duplicate"]
    rows = (await db.execute(select(Call).where(Call.org_id == org))).scalars().all()
    assert len(rows) == 1 and rows[0].status == "completed" and rows[0].duration_s == 42
    # another provider with the same id is a different call
    other = await client.post(f"/api/comms/calls/webhook?org_id={org}", json={**ev, "provider": "zadarma"}, headers=h)
    assert other.status_code == 201


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["javascript:alert(document.cookie)", " JavaScript:alert(1)",
                                 "data:text/html,<script>1</script>", "http://rec.example.com/a.mp3",
                                 "https://", "https://rec.example.com/a\"onmouseover=\"x"])
async def test_b9_recording_url_must_be_https(client, tenant, bad):
    org = await _org_id(client, tenant)
    h = {"X-Webhook-Token": await _token(client, tenant)}
    r = await client.post(f"/api/comms/calls/webhook?org_id={org}", json={"recording_url": bad}, headers=h)
    assert r.status_code == 422, bad


@pytest.mark.asyncio
async def test_b9_stored_bad_recording_url_is_not_rendered(client, tenant, db):
    org = await _org_id(client, tenant)
    db.add(Call(org_id=org, provider="manual", recording_url="javascript:alert(1)"))
    db.add(Call(org_id=org, provider="manual", recording_url="https://rec.example.com/ok.mp3"))
    await db.commit()
    items = (await client.get("/api/comms/calls", headers=tenant)).json()["items"]
    assert sorted(str(i["recording_url"]) for i in items) == ["None", "https://rec.example.com/ok.mp3"]


# ── B10: deliveries log needs can_manage_settings ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_b10_deliveries_need_manage_settings(client, tenant, db):
    from backend.tests.test_sensitive_reads import _restricted
    h, _, _ = await _restricted(client, db, ["can_manage_settings"])
    assert (await client.get("/api/webhooks/deliveries", headers=h)).status_code == 403
    assert (await client.get("/api/webhooks/deliveries", headers=tenant)).status_code == 200


# ── B12: Twilio SMS on the operator-wide token ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_b12_operator_token_checks_account_and_number(client, tenant, db, monkeypatch):
    from backend.services import org_creds, twilio_voice
    org = await _org_id(client, tenant)
    monkeypatch.setattr(settings, "TWILIO_AUTH_TOKEN", "op-token")
    monkeypatch.setattr(settings, "TWILIO_ACCOUNT_SID", "ACoperator")
    path = f"/api/chats/webhook/twilio?org_id={org}"

    async def post(form):
        sig = twilio_voice.signature("op-token", "http://test" + path, list(form.items()))
        return await client.post(path, data=form, headers={"X-Twilio-Signature": sig})

    base = {"From": "+15557770001", "Body": "hi", "MessageSid": "SM1"}
    assert (await post({**base, "AccountSid": "ACsomeoneelse", "To": "+15550009999"})).status_code == 403
    # no number registered for this shop: can't tell it's theirs
    assert (await post({**base, "AccountSid": "ACoperator", "To": "+15550009999"})).status_code == 403
    await org_creds.set_creds(db, org, "twilio", {"from": "+1 555 000 9999"})
    assert (await post({**base, "AccountSid": "ACoperator", "To": "+15550001111"})).status_code == 403
    ok = await post({**base, "AccountSid": "ACoperator", "To": "+15550009999"})
    assert ok.status_code == 200, ok.text


# ── B13: /view rate limit ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_b13_pay_link_view_is_rate_limited(client):
    codes = [(await client.post("/api/payments/intents/nope/view")).status_code for _ in range(61)]
    assert codes[:60] == [404] * 60 and codes[60] == 429


# ── B14: delete keeps the log ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_b14_delete_with_deliveries_is_a_soft_delete(client, tenant, db, dns):
    org = await _org_id(client, tenant)
    r = await client.post("/api/webhooks", headers=tenant, json={"url": "https://hooks.example.com/in"})
    eid = r.json()["id"]
    db.add(WebhookDelivery(org_id=org, endpoint_id=eid, event="e", payload="{}", status="sent", attempts=1))
    db.add(WebhookDelivery(org_id=org, endpoint_id=eid, event="e", payload="{}", status="failed", attempts=1,
                           next_attempt_at=datetime.utcnow()))
    await db.commit()
    assert (await client.delete(f"/api/webhooks/{eid}", headers=tenant)).status_code == 200
    assert (await client.get("/api/webhooks", headers=tenant)).json()["items"] == []
    rows = await _deliveries(db, org)
    assert sorted(d.status for d in rows) == ["abandoned", "sent"]
    assert (await client.delete(f"/api/webhooks/{eid}", headers=tenant)).status_code == 404
    log = (await client.get(f"/api/webhooks/deliveries?endpoint_id={eid}", headers=tenant)).json()["items"]
    assert len(log) == 2


# ── B15: management API (edit, test event, redeliver, delivery detail) ───────────────────────

@pytest.mark.asyncio
async def test_b15_edit_test_event_and_redeliver(client, tenant, db, dns, transport):
    org = await _org_id(client, tenant)
    r = await client.post("/api/webhooks", headers=tenant, json={
        "url": "https://hooks.example.com/in", "events": ["order.created"], "description": "ERP"})
    eid, secret = r.json()["id"], r.json()["secret"]
    p = await client.patch(f"/api/webhooks/{eid}", headers=tenant, json={
        "events": ["order.created", "invoice.paid"], "description": "ERP sync", "url": "https://erp.example.com/in"})
    assert p.status_code == 200
    ep = p.json()["endpoint"]
    assert ep["events"] == "order.created,invoice.paid" and ep["description"] == "ERP sync"
    assert ep["url"] == "https://erp.example.com/in"

    t = await client.post(f"/api/webhooks/{eid}/test", headers=tenant)
    assert t.status_code == 200 and t.json()["status"] == "sent" and t.json()["response_code"] == 200
    req = transport.seen[-1]
    assert req.headers["tekly-event"] == "webhook.test" and verify(secret, req.headers["tekly-signature"], req.content)

    transport.responses.append(httpx.Response(500))
    t2 = await client.post(f"/api/webhooks/{eid}/test", headers=tenant)
    assert t2.json()["status"] == "failed" and t2.json()["next_attempt_at"] is None   # tests aren't retried
    did = t2.json()["id"]
    detail = await client.get(f"/api/webhooks/deliveries/{did}", headers=tenant)
    assert detail.json()["payload"]["event"] == "webhook.test"
    again = await client.post(f"/api/webhooks/deliveries/{did}/redeliver", headers=tenant)
    assert again.status_code == 200 and again.json()["status"] == "sent", again.text
    ids = {r.headers["tekly-webhook-id"] for r in transport.seen[-2:]}
    assert ids == {did}                                              # same id on the redelivery
    other_org = await client.post("/api/auth/register", json={
        "org_name": "Other", "name": "O", "email": f"o-{uuid.uuid4().hex[:6]}@t.dev", "password": "pw-test-12345"})
    oh = {"Authorization": f"Bearer {other_org.json()['access_token']}"}
    assert (await client.post(f"/api/webhooks/deliveries/{did}/redeliver", headers=oh)).status_code == 404
    assert (await client.post(f"/api/webhooks/{eid}/test", headers=oh)).status_code == 404
    assert org


# ── B16: events from every source, one shape ─────────────────────────────────────────────────

async def _events(db, org):
    return [d.event for d in await _deliveries(db, org)]


async def _data(db, org, event):
    return [json.loads(d.payload)["data"] for d in await _deliveries(db, org, event)]


@pytest.mark.asyncio
async def test_b16_appointment_created_from_staff_and_any_insert(client, tenant, db):
    from backend.models import Appointment
    org = await _org_id(client, tenant)
    await _endpoint(db, org, "appointment.created")
    r = await client.post("/api/appointments", headers=tenant,
                          json={"client_name": "Jane", "scheduled_for": "2099-01-01T10:00:00"})
    assert r.status_code in (200, 201), r.text
    # public booking and every other path insert an Appointment row: the ORM hook covers them
    db.add(Appointment(org_id=org, client_name="Walk-in", scheduled_for=datetime(2099, 1, 2, 9), duration_min=30))
    await db.commit()
    data = await _data(db, org, "appointment.created")
    assert [d["client_name"] for d in data] == ["Jane", "Walk-in"]
    assert set(data[0]) >= {"id", "scheduled_for", "duration_min", "status", "client_id", "employee_id"}


@pytest.mark.asyncio
async def test_b16_api_appointment_is_not_sent_twice(client, tenant, db):
    from backend.models import Location
    org = await _org_id(client, tenant)
    await _endpoint(db, org, "appointment.created")
    key = (await client.post("/api/api-keys", json={"name": "z", "scopes": "*"}, headers=tenant)).json()
    auth = {"Authorization": f"Bearer {key.get('key') or key.get('token')}"}
    loc_id = (await db.execute(select(Location.id).where(Location.org_id == org))).scalars().first()
    r = await client.post("/api/v1/appointments", headers=auth,
                          json={"location_id": loc_id, "scheduled_for": "2099-07-01T14:00:00", "client_name": "Api"})
    assert r.status_code == 201, r.text
    assert await _events(db, org) == ["appointment.created"]


@pytest.mark.asyncio
async def test_b16_order_created_same_shape_from_app_and_api(client, tenant, db):
    org = await _org_id(client, tenant)
    await _endpoint(db, org, "order.created")
    key = (await client.post("/api/api-keys", json={"name": "z", "scopes": "*"}, headers=tenant)).json()
    auth = {"Authorization": f"Bearer {key.get('key') or key.get('token')}"}
    from backend.models import Location
    loc_id = (await db.execute(select(Location.id).where(Location.org_id == org))).scalars().first()
    assert (await client.post("/api/orders", json={"complaint": "app"}, headers=tenant)).status_code in (200, 201)
    r = await client.post("/api/v1/orders", json={"complaint": "api", "location_id": loc_id}, headers=auth)
    assert r.status_code == 201, r.text
    app, api = await _data(db, org, "order.created")
    assert set(app) == set(api)
    assert {"id", "order_number", "status", "total", "client_id", "created_at"} <= set(app)
    assert api["number"] == api["order_number"]          # deprecated alias, same in both shapes


@pytest.mark.asyncio
async def test_b16_contact_lead_invoice_events(client, tenant, db):
    org = await _org_id(client, tenant)
    await _endpoint(db, org, "contact.created,lead.created,invoice.created,invoice.paid")
    cid = (await client.post("/api/clients", json={"name": "Ada", "phone": "+15551112222"}, headers=tenant)).json()["id"]
    lead = await client.post("/api/leads", json={"name": "Bob", "phone": "+15553334444", "inquiry": "Cracked"}, headers=tenant)
    assert lead.status_code == 201, lead.text
    oid = (await client.post("/api/orders", json={"client_id": cid}, headers=tenant)).json()["id"]
    inv = (await client.post("/api/finance/invoices", json={"order_id": oid, "subtotal": 50}, headers=tenant)).json()
    assert "invoice.paid" not in await _events(db, org)
    pay = await client.post(f"/api/finance/invoices/{inv['id']}/pay", json={"method": "cash", "amount": 50}, headers=tenant)
    assert pay.status_code in (200, 201), pay.text
    ev = await _events(db, org)
    assert ev.count("contact.created") == 1 and ev.count("lead.created") == 1
    assert ev.count("invoice.created") == 1 and ev.count("invoice.paid") == 1
    (contact,) = await _data(db, org, "contact.created")
    assert contact["id"] == cid and contact["name"] == "Ada"
    (created,) = await _data(db, org, "invoice.created")
    assert created["id"] == inv["id"] and created["total"] == 50        # rendered at commit, not at insert
    (paid,) = await _data(db, org, "invoice.paid")
    assert paid["status"] == "paid" and paid["amount_paid"] == 50


@pytest.mark.asyncio
async def test_b16_order_updated_lists_changed_fields(client, tenant, db):
    org = await _org_id(client, tenant)
    oid = (await client.post("/api/orders", json={"complaint": "Screen"}, headers=tenant)).json()["id"]
    await _endpoint(db, org, "order.updated")
    r = await client.patch(f"/api/orders/{oid}/notes", json={"conclusion": "Replaced the screen", "tech_notes": "x"},
                           headers=tenant)
    assert r.status_code == 200, r.text
    (upd,) = await _data(db, org, "order.updated")
    assert upd["id"] == oid and upd["changed_fields"] == ["conclusion"]   # tech_notes is internal
    r = await client.patch(f"/api/orders/{oid}/notes", json={"conclusion": "Replaced the screen"}, headers=tenant)
    assert len(await _data(db, org, "order.updated")) == 1               # same value: no event


def test_b16_catalogue_in_one_place():
    for name in ("contact.created", "invoice.created", "invoice.paid", "lead.created", "order.updated",
                 "appointment.created", "order.created"):
        assert name in wh.EVENTS and wh.EVENT_CATALOGUE[name]
    assert wh.normalize_events("booking.created") == "booking.created"


def test_appointment_scheduled_for_is_wall_clock_without_z():
    """scheduled_for is shop wall-clock time; a trailing Z would tell receivers it is UTC."""
    from datetime import datetime
    from types import SimpleNamespace

    from backend.services.webhooks import appointment_data
    a = SimpleNamespace(id="a1", scheduled_for=datetime(2026, 10, 25, 9, 30, 15, 123), duration_min=60,
                        status="booked", client_id=None, client_name="Ana", service_id=None,
                        employee_id=None, location_id=None, order_id=None,
                        created_at=datetime(2026, 10, 25, 8, 0))
    d = appointment_data(a)
    assert d["scheduled_for"] == "2026-10-25T09:30:15"
    assert d["created_at"].endswith("Z")          # created_at is a real UTC instant
