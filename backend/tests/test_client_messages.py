"""Build 144ba — customer messages land in the work-order feed as client_message
items, bump the unread count, surface on board/list, and clear when staff read them."""
import pytest


async def _loc(client, H):
    d = (await client.get("/api/staff/locations", headers=H)).json()
    return (d if isinstance(d, list) else d["items"])[0]["id"]


async def _make_order(client, H):
    loc = await _loc(client, H)
    cid = (await client.post("/api/clients", json={"name": "Amit"}, headers=H)).json()["id"]
    return (await client.post("/api/orders", json={
        "client_id": cid, "location_id": loc, "complaint": "cracked screen"}, headers=H)).json()


async def test_track_message_lands_in_feed_and_bumps_unread(client, tenant):
    o = await _make_order(client, tenant)
    token = o["public_token"]
    # customer sends a message via the public track link (no auth)
    r = await client.post(f"/api/public/track/{token}/message", json={"body": "Is this under warranty?"})
    assert r.status_code == 200
    # it appears in the staff feed as a client_message
    feed = (await client.get(f"/api/feed/order/{o['id']}", headers=tenant)).json()["items"]
    msgs = [i for i in feed if i["item_type"] == "client_message"]
    assert msgs and msgs[0]["body"] == "Is this under warranty?"
    # empty message rejected
    assert (await client.post(f"/api/public/track/{token}/message", json={"body": "  "})).status_code == 400


async def test_unread_count_on_list_then_cleared_on_read(client, tenant):
    o = await _make_order(client, tenant)
    await client.post(f"/api/public/track/{o['public_token']}/message", json={"body": "hi"})
    await client.post(f"/api/public/track/{o['public_token']}/message", json={"body": "any update?"})
    # list serializer surfaces the unread count
    got = (await client.get(f"/api/orders/{o['id']}", headers=tenant)).json()
    assert got["client_msg_unread"] == 2
    # reading the feed (staff) clears it
    await client.get(f"/api/feed/order/{o['id']}", headers=tenant)
    again = (await client.get(f"/api/orders/{o['id']}", headers=tenant)).json()
    assert again["client_msg_unread"] == 0
