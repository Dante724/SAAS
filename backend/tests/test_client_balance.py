"""Build 116 — contact balance ledger: advance / payout / adjustment."""
import pytest


async def _new_client(client, tenant, name="Ledger Co"):
    r = await client.post("/api/clients", json={"name": name, "phone": "+15559999"}, headers=tenant)
    return r.json()["id"]


@pytest.mark.asyncio
async def test_advance_reduces_owed_balance(client, tenant):
    cid = await _new_client(client, tenant)
    r = await client.post(f"/api/clients/{cid}/balance", json={
        "kind": "advance", "amount": 500, "note": "Deposit"}, headers=tenant)
    assert r.status_code == 201, r.text
    assert r.json()["balance"] == -500.0   # advance = client paid in, we owe them

    bal = (await client.get(f"/api/clients/{cid}/balance", headers=tenant)).json()
    assert bal["balance"] == -500.0
    assert len(bal["entries"]) == 1
    assert bal["entries"][0]["entry_type"] == "advance"


@pytest.mark.asyncio
async def test_payout_increases_balance(client, tenant):
    cid = await _new_client(client, tenant)
    await client.post(f"/api/clients/{cid}/balance", json={"kind": "advance", "amount": 500}, headers=tenant)
    r = await client.post(f"/api/clients/{cid}/balance", json={"kind": "payout", "amount": 200}, headers=tenant)
    assert r.json()["balance"] == -300.0   # -500 advance + 200 payout returned


@pytest.mark.asyncio
async def test_adjustment_keeps_sign(client, tenant):
    cid = await _new_client(client, tenant)
    r = await client.post(f"/api/clients/{cid}/balance", json={
        "kind": "adjustment", "amount": -75, "note": "Write-off"}, headers=tenant)
    assert r.json()["balance"] == -75.0


@pytest.mark.asyncio
async def test_running_balance_matches_entries(client, tenant):
    cid = await _new_client(client, tenant)
    await client.post(f"/api/clients/{cid}/balance", json={"kind": "advance", "amount": 100}, headers=tenant)
    await client.post(f"/api/clients/{cid}/balance", json={"kind": "payout", "amount": 40}, headers=tenant)
    await client.post(f"/api/clients/{cid}/balance", json={"kind": "adjustment", "amount": 10}, headers=tenant)
    bal = (await client.get(f"/api/clients/{cid}/balance", headers=tenant)).json()
    running = sum(e["amount"] for e in bal["entries"])
    assert abs(running - bal["balance"]) < 0.001


@pytest.mark.asyncio
async def test_bad_kind_rejected(client, tenant):
    cid = await _new_client(client, tenant)
    r = await client.post(f"/api/clients/{cid}/balance", json={"kind": "steal", "amount": 5}, headers=tenant)
    assert r.status_code == 422
