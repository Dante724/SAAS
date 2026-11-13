"""Hardening council: a contact's running balance drifted from its ledger under concurrent
advances.

POST /api/clients/{id}/balance moved the balance with `c.balance = c.balance + signed` — read in
Python, written at commit — so six advances of 10 arriving together left the balance at -10
while the ledger summed to -60. The movement must be the same atomic UPDATE the rest of the
ledger uses (services.ledger.post_contact_ledger / money.add_to_column).
"""
import asyncio
import uuid

import pytest


def _keyed(tenant):
    """Re-review N9: a keyless repeat of the same movement within seconds is now refused as a double
    click; six deliberate advances each carry their own Idempotency-Key (as the dialog sends)."""
    return {**tenant, "Idempotency-Key": uuid.uuid4().hex}


async def _new_client(client, tenant, name="Race Co"):
    r = await client.post("/api/clients", json={"name": name}, headers=tenant)
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.mark.asyncio
async def test_client_balance_matches_ledger_under_concurrent_advances(client, tenant):
    cid = await _new_client(client, tenant)
    rs = await asyncio.gather(*[
        client.post(f"/api/clients/{cid}/balance", json={"kind": "advance", "amount": 10}, headers=_keyed(tenant))
        for _ in range(6)])
    assert all(r.status_code == 201 for r in rs), [r.text for r in rs]
    b = (await client.get(f"/api/clients/{cid}/balance", headers=tenant)).json()
    assert len(b["entries"]) == 6
    assert sum(e["amount"] for e in b["entries"]) == pytest.approx(-60)
    assert b["balance"] == pytest.approx(-60)


@pytest.mark.asyncio
async def test_client_balance_matches_ledger_under_concurrent_cash_advances(client, tenant):
    """Same race with a cashbox attached: the till and the contact both end up right."""
    acc = (await client.post("/api/cash/accounts", json={"name": "Till", "payment_option": "cash"},
                             headers=tenant)).json()["id"]
    cid = await _new_client(client, tenant)
    rs = await asyncio.gather(*[
        client.post(f"/api/clients/{cid}/balance",
                    json={"kind": "advance" if i % 2 else "payout", "amount": 10, "account_id": acc},
                    headers=_keyed(tenant))
        for i in range(6)])
    assert all(r.status_code == 201 for r in rs), [r.text for r in rs]
    b = (await client.get(f"/api/clients/{cid}/balance", headers=tenant)).json()
    assert sum(e["amount"] for e in b["entries"]) == pytest.approx(0)
    assert b["balance"] == pytest.approx(0)
    till = next(a for a in (await client.get("/api/cash/accounts", headers=tenant)).json()["items"]
                if a["id"] == acc)["balance"]
    assert till == pytest.approx(0)
