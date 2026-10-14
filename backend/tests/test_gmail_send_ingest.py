"""Build 144ae — Gmail send wiring + inbox polling ingest."""
import pytest

from backend.models import EmailConnection
from backend.services import email_oauth, notifications


async def _org_id(client, tenant):
    return (await client.get("/api/auth/me", headers=tenant)).json()["org_id"]


async def _connected_gmail(db, org_id, address="shop@gmail.com"):
    c = EmailConnection(org_id=org_id, provider="gmail", email_address=address,
                        connected=True, access_token="enc", is_default=True)
    db.add(c)
    await db.commit()
    return c


@pytest.mark.asyncio
async def test_notifications_prefer_connected_gmail(monkeypatch, db, client, tenant):
    org_id = await _org_id(client, tenant)
    await _connected_gmail(db, org_id)

    sent = []

    async def fake_send(conn, *, recipient, subject, body):
        sent.append({"to": recipient, "subject": subject})
        return {"status": "sent", "error": None}

    # the connected-account path used by dispatch
    monkeypatch.setattr(email_oauth, "send_via_connection", fake_send)

    # Clear the default rules seeded on org creation (build 144ah): order-create dispatches the
    # legacy template path only when NO rule matched, and a seeded order_created rule would
    # otherwise intercept it — so the connected-Gmail template below would never fire.
    from sqlalchemy import delete as _delete

    from backend.models import NotificationRule
    await db.execute(_delete(NotificationRule).where(NotificationRule.org_id == org_id))
    await db.commit()

    # a client with an email + an "email" notification template on order create
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    cid = (await client.post("/api/clients", json={"name": "Mia", "email": "mia@x.com"}, headers=tenant)).json()["id"]
    await client.post("/api/notifications/templates", json={
        "name": "Welcome", "channel": "email", "trigger_event": "order_created",
        "body_template": "Hi {client_name}, order {order_number} received."}, headers=tenant)
    await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x"}, headers=tenant)

    assert sent and sent[0]["to"] == "mia@x.com", "email was not sent through the connected Gmail account"


@pytest.mark.asyncio
async def test_poll_ingests_gmail_into_inbox(monkeypatch, db, client, tenant):
    org_id = await _org_id(client, tenant)
    await _connected_gmail(db, org_id)

    async def fake_list_recent(conn, max_results=12):
        return [
            {"message_id": "g1", "from": "ana@x.com", "from_name": "Ana <ana@x.com>",
             "subject": "My screen is cracked", "body": "Can you fix it today?"},
            {"message_id": "g2", "from": "ana@x.com", "from_name": "Ana <ana@x.com>",
             "subject": "Re: My screen", "body": "Also the battery drains fast"},
        ]

    monkeypatch.setattr(email_oauth, "list_recent", fake_list_recent)

    r = (await client.post("/api/integrations/email/poll", headers=tenant)).json()
    assert r["ingested"] == 2

    threads = (await client.get("/api/inbox/threads", headers=tenant)).json()
    items = threads.get("items", threads) if isinstance(threads, dict) else threads
    email_threads = [t for t in items if t.get("channel") == "email"]
    assert len(email_threads) == 1                 # both messages land in one thread (same sender)

    # polling again does not duplicate (de-dupe on Gmail message id)
    r2 = (await client.post("/api/integrations/email/poll", headers=tenant)).json()
    assert r2["ingested"] == 0


@pytest.mark.asyncio
async def test_list_recent_noops_without_read_scope(db, client, tenant):
    org_id = await _org_id(client, tenant)
    conn = await _connected_gmail(db, org_id)
    # not really configured / no live token → best-effort empty, never raises
    assert await email_oauth.list_recent(conn) == []
