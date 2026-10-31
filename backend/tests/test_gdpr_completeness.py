"""GDPR export and erasure reach every place a client's personal data lives (phase 4, audit C8).

Export returned only profile, assets and order headers; erasure cleared four profile fields
and left the portal link working, plus messages, chats, call recordings and booking contact
details untouched.
"""
from datetime import datetime

import pytest
from sqlalchemy import select


async def _seed(client, tenant, db):
    from backend.models import Appointment, ChatMessage, ChatThread, NotificationLog
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    cid = (await client.post("/api/clients", json={"name": "Jane Doe", "phone": "+15550001",
                                                   "email": "jane@example.test"}, headers=tenant)).json()["id"]
    org = me["org_id"]
    db.add(NotificationLog(org_id=org, client_id=cid, channel="sms", recipient="+15550001",
                           body="Hi Jane, your phone is ready", status="sent"))
    t = ChatThread(org_id=org, client_id=cid, contact_phone="+15550001", contact_name="Jane Doe")
    db.add(t)
    await db.flush()
    db.add(ChatMessage(thread_id=t.id, direction="in", body="my address is 1 Main St"))
    db.add(Appointment(org_id=org, client_id=cid, client_name="Jane Doe", client_phone="+15550001",
                       scheduled_for=datetime.utcnow()))
    await db.commit()
    return cid, t.id


@pytest.mark.asyncio
async def test_export_includes_messages_and_conversations(client, tenant, db):
    cid, _ = await _seed(client, tenant, db)
    r = await client.get(f"/api/compliance/gdpr/client/{cid}/export", headers=tenant)
    assert r.status_code == 200, r.text
    body = r.json()
    assert any("ready" in m["body"] for m in body["messages_sent"])
    assert any("Main St" in m["body"] for m in body["conversations"])
    assert body["appointments"] and "invoices" in body and "consent_history" in body


@pytest.mark.asyncio
async def test_erase_clears_related_personal_data(client, tenant, db):
    from backend.models import Appointment, ChatMessage, ChatThread, Client, NotificationLog
    cid, tid = await _seed(client, tenant, db)
    ptoken = (await client.post(f"/api/clients/{cid}/portal-link", headers=tenant)).json()["portal_token"]
    r = await client.post(f"/api/compliance/gdpr/client/{cid}/erase", headers=tenant)
    assert r.status_code == 200, r.text
    assert (await client.get(f"/api/public/portal/{ptoken}")).status_code == 404
    db.expire_all()
    cl = (await db.execute(select(Client).where(Client.id == cid))).scalar_one()
    from backend.models import ClientPortalLink
    assert cl.phone is None and "Jane" not in cl.name
    assert (await db.execute(select(ClientPortalLink).where(ClientPortalLink.client_id == cid))).first() is None
    log = (await db.execute(select(NotificationLog).where(NotificationLog.client_id == cid))).scalar_one()
    assert log.recipient is None and "Jane" not in log.body
    msg = (await db.execute(select(ChatMessage).where(ChatMessage.thread_id == tid))).scalar_one()
    assert "Main St" not in msg.body
    th = (await db.execute(select(ChatThread).where(ChatThread.id == tid))).scalar_one()
    assert th.contact_phone is None and th.contact_name is None
    ap = (await db.execute(select(Appointment).where(Appointment.client_id == cid))).scalar_one()
    assert ap.client_name is None and ap.client_phone is None
