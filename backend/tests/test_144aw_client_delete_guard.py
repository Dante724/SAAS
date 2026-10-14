"""Build 144aw (H3) — hard-deleting a contact that has history used to crash under enforced FKs
(it blindly ran db.delete(client)). We now refuse it with a 409 and point to merge, so financial
history is never silently cascaded away. A clean contact with no history still deletes."""
import pytest


@pytest.mark.asyncio
async def test_delete_contact_with_orders_is_refused(client, tenant):
    cid = (await client.post("/api/clients", json={"name": "Has History", "phone": "+1"}, headers=tenant)).json()["id"]
    await client.post("/api/orders", json={"client_id": cid, "complaint": "x"}, headers=tenant)
    r = await client.delete(f"/api/clients/{cid}", headers=tenant)
    assert r.status_code == 409, r.text
    # contact still there
    assert (await client.get(f"/api/clients/{cid}", headers=tenant)).status_code == 200


@pytest.mark.asyncio
async def test_delete_clean_contact_succeeds(client, tenant):
    cid = (await client.post("/api/clients", json={"name": "Nobody", "phone": "+2"}, headers=tenant)).json()["id"]
    r = await client.delete(f"/api/clients/{cid}", headers=tenant)
    assert r.status_code == 200, r.text
    assert (await client.get(f"/api/clients/{cid}", headers=tenant)).status_code == 404
