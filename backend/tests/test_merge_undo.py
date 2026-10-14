"""Build 144aj — a contact merge is reversible. Merge records a MergeLog (source snapshots +
what moved + the target's prior state); undo recreates the sources and puts everything back."""
import pytest


async def _client(client, tenant, **fields):
    return (await client.post("/api/clients", json=fields, headers=tenant)).json()["id"]


@pytest.mark.asyncio
async def test_merge_then_undo_restores_everything(client, tenant):
    target = await _client(client, tenant, name="Keep Co", phone="+15550001")
    source = await _client(client, tenant, name="Dupe Co", email="dupe@x.com")
    await client.post(f"/api/clients/{source}/balance", json={"kind": "advance", "amount": 200}, headers=tenant)
    oid = (await client.post("/api/orders", json={"client_id": source, "complaint": "fix"}, headers=tenant)).json()["id"]

    # Merge: source folds into target.
    m = (await client.post("/api/clients/merge", json={"target_id": target, "source_ids": [source]}, headers=tenant)).json()
    assert m["moved"]["orders"] == 1 and m["moved"]["ledger"] == 1, m
    log_id = m["merge_log_id"]
    # Post-merge: source gone, order + balance on target, target email filled from source.
    assert (await client.get(f"/api/clients/{source}", headers=tenant)).status_code == 404
    assert (await client.get(f"/api/orders/{oid}", headers=tenant)).json()["client"]["id"] == target
    tdetail = (await client.get(f"/api/clients/{target}", headers=tenant)).json()
    assert tdetail["email"] == "dupe@x.com"
    assert tdetail["balance"] == -200.0

    # Undo.
    u = await client.post(f"/api/clients/merge/{log_id}/undo", headers=tenant)
    assert u.status_code == 200, u.text

    # Source is back with its credit; the order is back on the source.
    src = (await client.get(f"/api/clients/{source}", headers=tenant)).json()
    assert src["id"] == source and src["email"] == "dupe@x.com"
    assert (await client.get(f"/api/orders/{oid}", headers=tenant)).json()["client"]["id"] == source
    # Target is restored to how it was: blank email, zero balance.
    tgt = (await client.get(f"/api/clients/{target}", headers=tenant)).json()
    assert tgt["email"] in (None, "")
    assert tgt["balance"] == 0.0


@pytest.mark.asyncio
async def test_undo_twice_is_rejected(client, tenant):
    target = await _client(client, tenant, name="A")
    source = await _client(client, tenant, name="B")
    log_id = (await client.post("/api/clients/merge", json={"target_id": target, "source_ids": [source]}, headers=tenant)).json()["merge_log_id"]
    assert (await client.post(f"/api/clients/merge/{log_id}/undo", headers=tenant)).status_code == 200
    assert (await client.post(f"/api/clients/merge/{log_id}/undo", headers=tenant)).status_code == 400
