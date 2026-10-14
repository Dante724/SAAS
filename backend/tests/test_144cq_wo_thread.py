"""144cq — Work-order customer conversation: one thread merging staff client-visible messages and
customer replies, chronologically, and reading it clears the unread counter."""
import pytest


async def _order(client, h):
    cid = (await client.post("/api/clients", json={"name": "Chat Cust", "phone": "+1"}, headers=h)).json()["id"]
    return (await client.post("/api/orders", json={"client_id": cid, "complaint": "screen"}, headers=h)).json()["id"]


@pytest.mark.asyncio
async def test_thread_merges_and_orders(client, tenant, db):
    oid = await _order(client, tenant)
    # staff posts a client-visible message
    r = await client.post(f"/api/orders/{oid}/thread", json={"body": "We received your device."}, headers=tenant)
    assert r.status_code == 201, r.text
    # simulate a customer reply (as the public track page would store it) + mark unread
    from backend.models import EventFeedItem, Order
    from sqlalchemy import select
    db.add(EventFeedItem(org_id=(await db.execute(select(Order).where(Order.id == oid))).scalar_one().org_id,
                         entity_type="order", entity_id=oid, item_type="client_message",
                         body="Great, when will it be ready?", author_name="Chat Cust", visibility="client"))
    o = (await db.execute(select(Order).where(Order.id == oid))).scalar_one()
    o.client_msg_unread = 1
    await db.commit()

    t = (await client.get(f"/api/orders/{oid}/thread", headers=tenant)).json()
    assert len(t["items"]) == 2, t
    assert t["items"][0]["from"] == "staff" and "received" in t["items"][0]["body"]
    assert t["items"][1]["from"] == "customer" and t["items"][1]["who"] == "Chat Cust"

    # reading the thread cleared the unread badge
    o2 = (await db.execute(select(Order).where(Order.id == oid))).scalar_one()
    await db.refresh(o2)
    assert o2.client_msg_unread == 0


@pytest.mark.asyncio
async def test_thread_rejects_empty(client, tenant):
    oid = await _order(client, tenant)
    r = await client.post(f"/api/orders/{oid}/thread", json={"body": "   "}, headers=tenant)
    assert r.status_code == 400, r.text
