"""Data-integrity regression: merging contacts must carry the contact's balance
ledger over to the target. Before the fix, merge_clients re-pointed devices/orders/
invoices/payments/feedback and summed the numeric balance, but left every
ClientLedgerEntry pointing at the source row — which is then hard-deleted. That
orphaned the audited store-credit history (and would FK-break on a real DB)."""
import pytest


async def _new_client(client, tenant, name, phone):
    r = await client.post("/api/clients", json={"name": name, "phone": phone}, headers=tenant)
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


@pytest.mark.asyncio
async def test_merge_moves_ledger_entries_to_target(client, tenant):
    target = await _new_client(client, tenant, "Keep Co", "+15550001")
    source = await _new_client(client, tenant, "Dupe Co", "+15550002")

    # Source has store credit: client paid in 300 (advance → we owe them 300).
    r = await client.post(f"/api/clients/{source}/balance", json={
        "kind": "advance", "amount": 300, "note": "Deposit on dupe"}, headers=tenant)
    assert r.status_code == 201, r.text

    # Merge the dupe into the keeper.
    r = await client.post("/api/clients/merge", json={
        "target_id": target, "source_ids": [source]}, headers=tenant)
    assert r.status_code == 200, r.text

    # The target's balance view now shows the moved entry and the carried-over amount.
    bal = (await client.get(f"/api/clients/{target}/balance", headers=tenant)).json()
    assert bal["balance"] == -300.0, bal
    assert len(bal["entries"]) == 1, bal
    assert bal["entries"][0]["entry_type"] == "advance"

    # The source is hard-deleted by the merge, so a ledger entry left pointing at it
    # would be orphaned — the moved entry showing up on the target (above) proves it
    # was re-pointed rather than abandoned. The source balance view 404s now.
    assert (await client.get(f"/api/clients/{source}/balance", headers=tenant)).status_code == 404
