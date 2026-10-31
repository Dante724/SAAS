"""Tekly Go messaging: templates filled in on the server, consent respected, and a text the
person confirms as sent shows in the job history and the customer's portal thread."""
import uuid

import pytest
from sqlalchemy import select

from backend.services.phones import to_e164
from backend.tests.test_sensitive_reads import _restricted


def test_to_e164():
    assert to_e164("+44 7700 900123", None) == "+447700900123"
    assert to_e164("07700 900123", "GB") == "+447700900123"
    assert to_e164("0044 7700 900123", "IN") == "+447700900123"
    assert to_e164("98765 43210", "IN") == "+919876543210"
    assert to_e164("919876543210", "IN") == "+919876543210"     # already carries the code
    assert to_e164("(555) 014-2233", "US") == "+15550142233"
    assert to_e164("12345", "GB") is None                      # too short to be real
    assert to_e164("0412 345 678", None) is None                # no country to place it in
    assert to_e164("", "GB") is None


async def _job(client, h, **client_fields):
    c = (await client.post("/api/clients", headers=h, json={
        "name": "Sara Khan", "phone": f"+44 7700 9{uuid.uuid4().int % 10**5:05d}", **client_fields})).json()
    o = (await client.post("/api/orders", headers=h, json={"client_id": c["id"], "complaint": "screen"})).json()
    return c, o


@pytest.mark.asyncio
async def test_compose_fills_templates_for_this_job(client, tenant):
    await client.post("/api/clients/message-templates/all", headers=tenant, json={
        "name": "Ready", "channel": "sms", "body": "Hi {Client First Name}, {Order Number} is ready."})
    await client.post("/api/clients/message-templates/all", headers=tenant, json={
        "name": "Email only", "channel": "email", "body": "x"})
    c, o = await _job(client, tenant)
    r = await client.get(f"/api/mobile/orders/{o['id']}/compose", headers=tenant)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["to_e164"].startswith("+447700")
    assert d["consent"] == {"sms": True, "whatsapp": None}
    names = [t["name"] for t in d["templates"]]
    assert "Ready" in names and "Email only" not in names
    body = next(t["body"] for t in d["templates"] if t["name"] == "Ready")
    assert body == f"Hi Sara, {o['order_number']} is ready."


@pytest.mark.asyncio
async def test_sent_text_lands_in_portal_thread_and_history(client, tenant):
    c, o = await _job(client, tenant)
    h = await client.post("/api/mobile/messages", headers=tenant, json={
        "order_id": o["id"], "channel": "sms", "body": "Your phone is ready to collect"})
    assert h.status_code == 201, h.text
    hid = h.json()["id"]
    # nothing reaches the customer until the person says it was sent
    thread = (await client.get(f"/api/orders/{o['id']}/thread", headers=tenant)).json()["items"]
    assert not any(m["body"] == "Your phone is ready to collect" for m in thread)
    r = await client.patch(f"/api/mobile/messages/{hid}", headers=tenant, json={"state": "sent"})
    assert r.json()["state"] == "sent"
    thread = (await client.get(f"/api/orders/{o['id']}/thread", headers=tenant)).json()["items"]
    assert any(m["body"] == "Your phone is ready to collect" and m["from"] == "staff" for m in thread)
    feed = (await client.get(f"/api/feed/order/{o['id']}", headers=tenant)).json()
    items = feed.get("items", feed) if isinstance(feed, dict) else feed
    assert any("sent a SMS from their phone" in (i.get("body") or "") for i in items)
    # a retry is harmless; flipping it afterwards isn't allowed
    assert (await client.patch(f"/api/mobile/messages/{hid}", headers=tenant, json={"state": "sent"})).status_code == 200
    assert (await client.patch(f"/api/mobile/messages/{hid}", headers=tenant, json={"state": "cancelled"})).status_code == 409


@pytest.mark.asyncio
async def test_cancelled_text_stays_out_of_the_portal(client, tenant):
    c, o = await _job(client, tenant)
    hid = (await client.post("/api/mobile/messages", headers=tenant, json={
        "order_id": o["id"], "channel": "sms", "body": "Draft I didn't send"})).json()["id"]
    await client.patch(f"/api/mobile/messages/{hid}", headers=tenant, json={"state": "cancelled"})
    thread = (await client.get(f"/api/orders/{o['id']}/thread", headers=tenant)).json()["items"]
    assert not any(m["body"] == "Draft I didn't send" for m in thread)


@pytest.mark.asyncio
async def test_whatsapp_needs_a_yes_first(client, db, tenant):
    from backend.models import ConsentLog
    c, o = await _job(client, tenant)
    body = {"order_id": o["id"], "channel": "whatsapp", "body": "Hi"}
    r = await client.post("/api/mobile/messages", headers=tenant, json=body)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "consent_required"
    g = await client.post(f"/api/mobile/clients/{c['id']}/whatsapp-consent", headers=tenant, json={"granted": True})
    assert g.json()["consent"]["whatsapp"] is True
    assert (await client.post("/api/mobile/messages", headers=tenant, json=body)).status_code == 201
    log = (await db.execute(select(ConsentLog).where(ConsentLog.client_id == c["id"], ConsentLog.channel == "whatsapp"))).scalars().all()
    assert len(log) == 1 and log[0].granted and log[0].source == "mobile_verbal"


@pytest.mark.asyncio
async def test_no_texts_to_a_customer_who_said_no(client, tenant):
    c, o = await _job(client, tenant, consent_phone=False)
    r = await client.post("/api/mobile/messages", headers=tenant, json={"order_id": o["id"], "channel": "sms", "body": "Hi"})
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_messaging_needs_can_send_sms(client, db, tenant):
    c, o = await _job(client, tenant)
    h, _, _ = await _restricted(client, db, ["can_send_sms"])
    assert (await client.get(f"/api/mobile/orders/{o['id']}/compose", headers=h)).status_code == 403
    assert (await client.post("/api/mobile/messages", headers=h, json={"order_id": o["id"], "channel": "sms", "body": "x"})).status_code == 403


@pytest.mark.asyncio
async def test_only_the_sender_marks_it(client, db, tenant):
    c, o = await _job(client, tenant)
    hid = (await client.post("/api/mobile/messages", headers=tenant, json={
        "order_id": o["id"], "channel": "sms", "body": "Hi"})).json()["id"]
    h, _, _ = await _restricted(client, db, [])
    assert (await client.patch(f"/api/mobile/messages/{hid}", headers=h, json={"state": "sent"})).status_code == 403


@pytest.mark.asyncio
async def test_erasure_scrubs_phone_messages(client, db, tenant):
    from backend.models import MessageHandoff
    c, o = await _job(client, tenant)
    await client.post("/api/mobile/messages", headers=tenant, json={"order_id": o["id"], "channel": "sms", "body": "Sara, at 12 Elm St?"})
    r = await client.post(f"/api/compliance/gdpr/client/{c['id']}/erase", headers=tenant, json={"confirm": True})
    assert r.status_code in (200, 201), r.text
    db.expire_all()
    rows = (await db.execute(select(MessageHandoff).where(MessageHandoff.client_id == c["id"]))).scalars().all()
    assert rows and all(h.to_number is None and "Elm" not in h.body for h in rows)


@pytest.mark.asyncio
async def test_template_links_are_absolute(client, tenant):
    await client.post("/api/clients/message-templates/all", headers=tenant, json={
        "name": "Track", "channel": "sms", "body": "Track it: {Tracking URL}"})
    c, o = await _job(client, tenant)
    d = (await client.get(f"/api/mobile/orders/{o['id']}/compose", headers=tenant)).json()
    body = next(t["body"] for t in d["templates"] if t["name"] == "Track")
    assert "://" in body and "/track/" in body
