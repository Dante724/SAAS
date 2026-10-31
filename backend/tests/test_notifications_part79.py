"""Part 9 (preferences + triggers) and Part 7 (customer/device history)."""
from datetime import datetime

import pytest
from sqlalchemy import select

from backend.services.dates import now_utc


async def _me(client, tenant):
    return (await client.get("/api/auth/me", headers=tenant)).json()


async def _loc(client, tenant):
    return (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]


async def _client_rec(client, tenant, name="Ada Lovelace"):
    r = await client.post("/api/clients", json={"name": name}, headers=tenant)
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


# ── Part 9: preferences ───────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_preferences_default_on_and_toggle(client, tenant):
    prefs = (await client.get("/api/tech/preferences", headers=tenant)).json()["preferences"]
    assert prefs and all(p["enabled"] for p in prefs)        # on by default
    r = await client.put("/api/tech/preferences", json={"kind": "appointment.cancelled", "enabled": False}, headers=tenant)
    assert r.status_code == 200 and r.json()["enabled"] is False
    prefs2 = {p["kind"]: p["enabled"] for p in (await client.get("/api/tech/preferences", headers=tenant)).json()["preferences"]}
    assert prefs2["appointment.cancelled"] is False
    assert (await client.put("/api/tech/preferences", json={"kind": "nope", "enabled": False}, headers=tenant)).status_code == 422


@pytest.mark.asyncio
async def test_muted_kind_suppresses_notification(client, tenant, db):
    from backend.services.push import notify
    me = await _me(client, tenant)
    await client.put("/api/tech/preferences", json={"kind": "chat.message", "enabled": False}, headers=tenant)
    n = await notify(db, org_id=me["org_id"], employee_id=me["id"], kind="chat.message", title="hi")
    assert n is None                                          # muted -> nothing created
    # a non-muted kind still works
    n2 = await notify(db, org_id=me["org_id"], employee_id=me["id"], kind="payment.received", title="paid")
    assert n2 is not None
    unread = (await client.get("/api/tech/dashboard", headers=tenant)).json()["unread_notifications"]
    assert unread >= 1


# ── Part 9: triggers ───────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_payment_received_notifies_assigned_tech(client, tenant, db):
    from backend.models import StaffNotification
    me = await _me(client, tenant)
    loc = await _loc(client, tenant)
    o = (await client.post("/api/orders", json={"location_id": loc, "complaint": "x", "assigned_to_id": me["id"]}, headers=tenant)).json()
    await client.post(f"/api/orders/{o['id']}/lines", json={"line_type": "part", "kind": "custom", "description": "p", "qty": 1, "unit_price": 40.0}, headers=tenant)
    link = await client.post(f"/api/orders/{o['id']}/payment-link", json={}, headers=tenant)
    assert link.status_code == 201, link.text
    sim = await client.post(f"/api/payments/intents/{link.json()['intent_id']}/simulate-pay")
    assert sim.status_code == 200
    notes = (await db.execute(select(StaffNotification).where(
        StaffNotification.employee_id == me["id"], StaffNotification.kind == "payment.received"))).scalars().all()
    assert notes, "assigned tech should be notified of payment"


@pytest.mark.asyncio
async def test_estimate_approved_notifies_assigned_tech(client, tenant, db):
    from backend.models import StaffNotification
    await client.get("/api/statuses?is_estimate=true", headers=tenant)   # seed estimate statuses
    me = await _me(client, tenant)
    cid = await _client_rec(client, tenant, "Estimate Client")
    loc = await _loc(client, tenant)
    est = (await client.post("/api/orders", json={
        "location_id": loc, "client_id": cid, "is_estimate": True, "assigned_to_id": me["id"]}, headers=tenant)).json()
    token = (await client.post(f"/api/clients/{cid}/portal-link", headers=tenant)).json()["portal_token"]
    resp = await client.post(f"/api/public/portal/{token}/order/{est['id']}/respond", json={"decision": "accept"})
    assert resp.status_code == 200, resp.text
    notes = (await db.execute(select(StaffNotification).where(
        StaffNotification.employee_id == me["id"], StaffNotification.kind == "estimate.approved"))).scalars().all()
    assert notes


@pytest.mark.asyncio
async def test_appointment_cancelled_notifies_booked_tech(client, tenant, db):
    from backend.models import Appointment, Employee, StaffNotification
    from backend.services.auth import hash_password
    me = await _me(client, tenant)
    loc = await _loc(client, tenant)
    tech = Employee(org_id=me["org_id"], name="Booked Tech", email="bt@iso.dev",
                    password_hash=hash_password("pw-test-12345"), active=True)
    db.add(tech)
    await db.commit()
    await db.refresh(tech)
    b = Appointment(org_id=me["org_id"], location_id=loc, employee_id=tech.id, status="booked",
                client_name="Walk in", scheduled_for=now_utc(), duration_min=30)
    db.add(b)
    await db.commit()
    await db.refresh(b)
    r = await client.patch(f"/api/appointments/{b.id}", json={"status": "canceled"}, headers=tenant)
    assert r.status_code == 200, r.text
    notes = (await db.execute(select(StaffNotification).where(
        StaffNotification.employee_id == tech.id, StaffNotification.kind == "appointment.cancelled"))).scalars().all()
    assert notes


# ── Part 7: history ─────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_client_history_aggregation(client, tenant):
    cid = await _client_rec(client, tenant, "History Client")
    loc = await _loc(client, tenant)
    o1 = (await client.post("/api/orders", json={"location_id": loc, "client_id": cid, "complaint": "fix"}, headers=tenant)).json()
    await client.post(f"/api/orders/{o1['id']}/lines", json={"line_type": "part", "kind": "custom", "description": "scr", "qty": 1, "unit_price": 60.0}, headers=tenant)
    await client.post(f"/api/orders/{o1['id']}/comments", json={"body": "Recommend battery replacement", "visible_to_client": True}, headers=tenant)
    (await client.post("/api/orders", json={"location_id": loc, "client_id": cid, "complaint": "two"}, headers=tenant)).json()

    h = (await client.get(f"/api/tech/client/{cid}/history", headers=tenant)).json()
    assert h["client"]["name"] == "History Client"
    assert h["stats"]["orders"] >= 2
    assert h["stats"]["outstanding_balance"] >= 60.0
    assert any("battery" in r["body"].lower() for r in h["recommendations"])
    assert len(h["orders"]) >= 2
    assert (await client.get("/api/tech/client/does-not-exist/history", headers=tenant)).status_code == 404


@pytest.mark.asyncio
async def test_device_history(client, tenant, db):
    from backend.models import Device
    me = await _me(client, tenant)
    cid = await _client_rec(client, tenant, "Device Owner")
    loc = await _loc(client, tenant)
    dev = Device(org_id=me["org_id"], client_id=cid, asset_type="phone", brand="Acme", model="X1", serial="SN-123")
    db.add(dev)
    await db.commit()
    await db.refresh(dev)
    o = (await client.post("/api/orders", json={"location_id": loc, "client_id": cid, "device_id": dev.id, "complaint": "cracked"}, headers=tenant)).json()
    h = (await client.get(f"/api/tech/device/{dev.id}/history", headers=tenant)).json()
    assert h["device"]["serial"] == "SN-123"
    assert h["stats"]["repairs"] >= 1
    assert any(c["id"] == o["id"] for c in h["orders"])


# ── Part 9: real Web Push dispatch (wiring proven via a stubbed transport) ─────
@pytest.mark.asyncio
async def test_real_push_send_invokes_webpush(client, tenant, db, monkeypatch):
    import pywebpush

    from backend.config import settings
    from backend.models import PushSubscription
    from backend.services.push import notify

    me = (await client.get("/api/auth/me", headers=tenant)).json()
    db.add(PushSubscription(org_id=me["org_id"], employee_id=me["id"],
                            endpoint="https://push.example/ep1", p256dh="pk", auth="ak"))
    await db.commit()

    # Configure VAPID so push_enabled() flips true, and capture the transport call.
    monkeypatch.setattr(settings, "VAPID_PUBLIC_KEY", "test-pub")
    monkeypatch.setattr(settings, "VAPID_PRIVATE_KEY", "test-priv")
    calls = []
    monkeypatch.setattr(pywebpush, "webpush", lambda **kw: calls.append(kw))

    await notify(db, org_id=me["org_id"], employee_id=me["id"],
                 kind="job.assigned", title="Real push", body="hello")

    assert len(calls) == 1
    kw = calls[0]
    assert kw["subscription_info"]["endpoint"] == "https://push.example/ep1"
    assert kw["subscription_info"]["keys"] == {"p256dh": "pk", "auth": "ak"}
    assert kw["vapid_private_key"] == "test-priv"
    assert '"title": "Real push"' in kw["data"]


@pytest.mark.asyncio
async def test_push_prunes_dead_subscription(client, tenant, db, monkeypatch):
    import pywebpush
    from sqlalchemy import select

    from backend.config import settings
    from backend.models import PushSubscription
    from backend.services.push import notify

    me = (await client.get("/api/auth/me", headers=tenant)).json()
    db.add(PushSubscription(org_id=me["org_id"], employee_id=me["id"],
                            endpoint="https://push.example/dead", p256dh="pk", auth="ak"))
    await db.commit()
    monkeypatch.setattr(settings, "VAPID_PUBLIC_KEY", "test-pub")
    monkeypatch.setattr(settings, "VAPID_PRIVATE_KEY", "test-priv")

    class Gone(Exception):
        response = type("R", (), {"status_code": 410})()

    def boom(**kw):
        raise Gone()
    monkeypatch.setattr(pywebpush, "webpush", boom)

    await notify(db, org_id=me["org_id"], employee_id=me["id"], kind="job.assigned", title="x")
    left = (await db.execute(select(PushSubscription).where(
        PushSubscription.endpoint == "https://push.example/dead"))).scalar_one_or_none()
    assert left is None   # 410 Gone -> pruned


# ── Part 9: chat.message trigger on inbound customer message ───────────────────
@pytest.mark.asyncio
async def test_inbound_chat_notifies_assigned_tech(client, tenant, db):
    from sqlalchemy import select

    from backend.models import ChatThread, Client, StaffNotification

    me = (await client.get("/api/auth/me", headers=tenant)).json()
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    cl = Client(org_id=me["org_id"], name="Chatty Cathy")
    db.add(cl)
    await db.commit()
    await db.refresh(cl)
    # an open order for this client assigned to me
    await client.post("/api/orders", json={
        "location_id": loc, "client_id": cl.id, "assigned_to_id": me["id"], "complaint": "x"}, headers=tenant)
    th = ChatThread(org_id=me["org_id"], channel="sms", contact_phone="+15551234", client_id=cl.id)
    db.add(th)
    await db.commit()
    await db.refresh(th)

    r = await client.post(f"/api/chats/threads/{th.id}/inbound",
                          json={"body": "is my phone ready?"}, headers=tenant)
    assert r.status_code == 201, r.text
    notes = (await db.execute(select(StaffNotification).where(
        StaffNotification.employee_id == me["id"], StaffNotification.kind == "chat.message"))).scalars().all()
    assert notes, "inbound chat should notify the assigned technician"
