"""A1 — transaction corrections + soft-delete + restore (append-only cash ledger)."""
import pytest


async def _account(client, headers):
    r = await client.post("/api/cash/accounts", json={
        "name": "Till", "payment_option": "cash"}, headers=headers)
    assert r.status_code in (200, 201), r.text
    return r.json()["id"] if "id" in r.json() else (
        await client.get("/api/cash/accounts", headers=headers)).json()["items"][0]["id"]


async def _accounts(client, headers):
    return (await client.get("/api/cash/accounts", headers=headers)).json()["items"]


@pytest.mark.asyncio
async def test_income_then_correct_delete_restore(client, tenant):
    h = tenant
    # make an account
    await client.post("/api/cash/accounts", json={"name": "Till", "payment_option": "cash"}, headers=h)
    acct = (await _accounts(client, h))[0]
    aid = acct["id"]

    # post income 100
    r = await client.post("/api/cash/transactions", json={
        "account_id": aid, "txn_type": "income", "amount": 100, "comment": "sale"}, headers=h)
    assert r.status_code == 201, r.text

    txns = (await client.get(f"/api/cash/transactions?account_id={aid}", headers=h)).json()["items"]
    assert len(txns) == 1
    t = txns[0]
    assert t["status"] == "active"
    assert t["balance_after"] == 100  # frozen snapshot

    # correct: reduce by 30 (mandatory reason)
    r = await client.post(f"/api/cash/transactions/{t['id']}/correct",
                          json={"delta": -30, "reason": "overcharged"}, headers=h)
    assert r.status_code == 201, r.text

    # account balance now 70; original untouched + flagged corrected; delta row present
    accts = await _accounts(client, h)
    assert accts[0]["balance"] == 70
    txns = (await client.get(f"/api/cash/transactions?account_id={aid}", headers=h)).json()["items"]
    by_id = {x["id"]: x for x in txns}
    assert by_id[t["id"]]["amount"] == 100          # original never mutated
    assert by_id[t["id"]]["status"] == "corrected"
    delta = [x for x in txns if x.get("reverses_txn_id") == t["id"]][0]
    assert delta["amount"] == -30
    assert "overcharged" in (delta["comment"] or "")

    # correction requires a reason
    r = await client.post(f"/api/cash/transactions/{t['id']}/correct",
                          json={"delta": -5, "reason": "  "}, headers=h)
    assert r.status_code == 400

    # soft-delete the delta row → balance back to 100, row still present, struck
    r = await client.delete(f"/api/cash/transactions/{delta['id']}?reason=mistake", headers=h)
    assert r.status_code == 200, r.text
    assert (await _accounts(client, h))[0]["balance"] == 100
    txns = (await client.get(f"/api/cash/transactions?account_id={aid}", headers=h)).json()["items"]
    d = [x for x in txns if x["id"] == delta["id"]][0]
    assert d["status"] == "deleted" and d["void_reason"] == "mistake"

    # frozen history: the ORIGINAL row's balance_after is still 100, unchanged by the delete
    assert [x for x in txns if x["id"] == t["id"]][0]["balance_after"] == 100

    # restore the delta row → balance 70 again
    r = await client.post(f"/api/cash/transactions/{delta['id']}/restore", headers=h)
    assert r.status_code == 200, r.text
    assert (await _accounts(client, h))[0]["balance"] == 70

    # cannot delete an already-deleted row
    await client.delete(f"/api/cash/transactions/{delta['id']}", headers=h)
    r = await client.delete(f"/api/cash/transactions/{delta['id']}", headers=h)
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_delete_transfer_voids_both_legs(client, tenant):
    h = tenant
    await client.post("/api/cash/accounts", json={"name": "A", "payment_option": "cash"}, headers=h)
    await client.post("/api/cash/accounts", json={"name": "B", "payment_option": "cash"}, headers=h)
    accts = await _accounts(client, h)
    a, b = accts[0]["id"], accts[1]["id"]
    # fund A with 200
    await client.post("/api/cash/transactions", json={"account_id": a, "txn_type": "income", "amount": 200}, headers=h)
    # transfer 50 A→B
    r = await client.post("/api/cash/transfer", json={
        "from_account_id": a, "to_account_id": b, "amount": 50}, headers=h)
    assert r.status_code == 201, r.text
    accts = {x["name"]: x for x in await _accounts(client, h)}
    assert accts["A"]["balance"] == 150 and accts["B"]["balance"] == 50

    # delete one leg → both legs void, balances revert
    legs = (await client.get(f"/api/cash/transactions?account_id={b}", headers=h)).json()["items"]
    leg = legs[0]
    r = await client.delete(f"/api/cash/transactions/{leg['id']}", headers=h)
    assert r.status_code == 200 and r.json()["voided"] == 2
    accts = {x["name"]: x for x in await _accounts(client, h)}
    assert accts["A"]["balance"] == 200 and accts["B"]["balance"] == 0
