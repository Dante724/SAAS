"""Build 144ai — merge preview (dry-run). The front desk sees exactly what a merge will move
and which blank fields fill, before the irreversible merge runs. The preview must change nothing."""
import pytest


async def _client(client, tenant, **fields):
    return (await client.post("/api/clients", json=fields, headers=tenant)).json()["id"]


@pytest.mark.asyncio
async def test_preview_reports_moves_and_fills_without_committing(client, tenant):
    # Target has a phone but no email; source has store credit, an email, and an order's worth of history.
    target = await _client(client, tenant, name="Keep Co", phone="+15550001")
    source = await _client(client, tenant, name="Dupe Co", email="dupe@x.com")
    await client.post(f"/api/clients/{source}/balance", json={"kind": "advance", "amount": 150}, headers=tenant)

    r = await client.post("/api/clients/merge/preview", json={"target_id": target, "source_ids": [source]}, headers=tenant)
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["moved"]["ledger"] == 1, p
    assert p["result_balance"] == -150.0, p          # target 0 + source advance (-150)
    fills = {f["field"]: f["value"] for f in p["field_fills"]}
    assert fills.get("email") == "dupe@x.com", p      # target's blank email would fill from source
    assert "phone" not in fills                        # target already has a phone — never overwritten

    # Nothing changed: both contacts still exist and the source keeps its credit.
    assert (await client.get(f"/api/clients/{source}", headers=tenant)).status_code == 200
    assert (await client.get(f"/api/clients/{target}", headers=tenant)).json().get("email") in (None, "")


@pytest.mark.asyncio
async def test_preview_rejects_target_as_source(client, tenant):
    cid = await _client(client, tenant, name="Solo")
    r = await client.post("/api/clients/merge/preview", json={"target_id": cid, "source_ids": [cid]}, headers=tenant)
    assert r.status_code == 400, r.text
