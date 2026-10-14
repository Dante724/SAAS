"""144ct — close the comms loop on the PUBLIC side: a staff reply (posted from the
work-order ConversationPanel as a client-visible comment) must appear to the customer on
their track page, not just in the staff thread. The customer→shop direction already worked."""
import pytest


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


@pytest.mark.asyncio
async def test_staff_reply_visible_on_track_page(client, tenant):
    loc = await _loc(client, tenant)
    cid = (await client.post("/api/clients", json={"name": "Dana"}, headers=tenant)).json()["id"]
    o = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x"},
                            headers=tenant)).json()
    token = (await client.get(f"/api/orders/{o['id']}", headers=tenant)).json()["public_token"]
    assert token

    # customer sends a message from the public track page
    r = await client.post(f"/api/public/track/{token}/message", json={"body": "When is it ready?"})
    assert r.status_code == 200, r.text

    # staff replies from the work-order conversation panel
    r2 = await client.post(f"/api/orders/{o['id']}/thread", json={"body": "Ready by 5pm today."}, headers=tenant)
    assert r2.status_code == 201, r2.text

    # the customer's track page now shows BOTH their message and the staff reply
    pub = (await client.get(f"/api/public/track/{token}")).json()
    bodies = [u.get("body") for u in pub.get("updates", [])]
    assert "When is it ready?" in bodies, "customer message missing from track updates"
    assert "Ready by 5pm today." in bodies, "staff reply not surfaced to the customer"
