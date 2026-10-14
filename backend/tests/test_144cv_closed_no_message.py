"""144cv — once a work order is CLOSED (terminal status) the customer can no longer message
the shop from the track page. Reopening the order restores messaging. Based on the order's
current status being terminal, so it reflects reopen correctly (closed_at is not cleared on reopen)."""
import pytest


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


async def _order_with_token(client, h):
    loc = await _loc(client, h)
    cid = (await client.post("/api/clients", json={"name": "C"}, headers=h)).json()["id"]
    o = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x"},
                            headers=h)).json()
    token = (await client.get(f"/api/orders/{o['id']}", headers=h)).json()["public_token"]
    return o, token


@pytest.mark.asyncio
async def test_message_blocked_once_closed_restored_on_reopen(client, tenant):
    o, token = await _order_with_token(client, tenant)

    # open order: messaging works
    r = await client.post(f"/api/public/track/{token}/message", json={"body": "hi"})
    assert r.status_code == 200, r.text

    # close the order (terminal status)
    rc = await client.patch(f"/api/orders/{o['id']}/status", json={"status": "done"}, headers=tenant)
    assert rc.status_code == 200, rc.text

    # messaging now rejected
    r2 = await client.post(f"/api/public/track/{token}/message", json={"body": "still there?"})
    assert r2.status_code == 403, f"closed order should reject messages, got {r2.status_code}"

    # reopen → messaging works again
    rr = await client.patch(f"/api/orders/{o['id']}/status", json={"status": "in_repair"}, headers=tenant)
    assert rr.status_code == 200, rr.text
    r3 = await client.post(f"/api/public/track/{token}/message", json={"body": "one more"})
    assert r3.status_code == 200, r3.text
