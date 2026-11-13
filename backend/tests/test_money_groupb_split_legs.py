"""Re-review money N7: a contact cash leg voided alone, or corrected, before #213 is reported by the
nightly split_leg sweep forever (it can no longer be deleted or corrected). The owner can now accept
it with a reason (the contact side was settled by hand), which clears the sweep; an accepted voided
leg can't then be restored (that would put the till off by the same amount again)."""
from backend.models import Transaction
from backend.services import reconcile
from backend.tests.test_p1_money_audit import _acct, _me


async def _legs(db, org, till):
    voided = Transaction(org_id=org, account_id=till, txn_type="income", amount=50, ref_type="client",
                         ref_id="c1", status="deleted", void_reason="pre-#213 void")
    db.add(voided)
    await db.flush()
    orig = Transaction(org_id=org, account_id=till, txn_type="income", amount=20, ref_type="supplier_payment",
                       ref_id="s1", status="corrected")
    db.add(orig)
    await db.flush()
    delta = Transaction(org_id=org, account_id=till, txn_type="income", amount=5, ref_type="supplier_payment",
                        ref_id="s1", reverses_txn_id=orig.id)
    db.add(delta)
    await db.commit()
    return voided.id, delta.id


async def test_owner_can_accept_a_legacy_split_leg_and_the_sweep_stops_reporting_it(client, tenant, db):
    org = (await _me(client, tenant))["org_id"]
    till = await _acct(client, tenant, "Till")
    voided, delta = await _legs(db, org, till)
    found = {m.entity_id for m in await reconcile._split_legs(db, org)}
    assert {voided, delta} <= found

    r = await client.post(f"/api/cash/transactions/{voided}/accept-split", json={"reason": ""}, headers=tenant)
    assert r.status_code == 400, r.text                     # a reason is required
    for tid in (voided, delta):
        r = await client.post(f"/api/cash/transactions/{tid}/accept-split",
                              json={"reason": "client side settled by hand"}, headers=tenant)
        assert r.status_code == 200, r.text
    found = {m.entity_id for m in await reconcile._split_legs(db, org)}
    assert not ({voided, delta} & found), found

    # accepted: restoring the voided leg would count the money twice
    r = await client.post(f"/api/cash/transactions/{voided}/restore", headers=tenant)
    assert r.status_code == 409, r.text


async def test_accept_split_only_takes_a_split_contact_leg(client, tenant, db):
    org = (await _me(client, tenant))["org_id"]
    till = await _acct(client, tenant, "Till")
    t = Transaction(org_id=org, account_id=till, txn_type="income", amount=10, ref_type="manual")
    db.add(t)
    await db.commit()
    r = await client.post(f"/api/cash/transactions/{t.id}/accept-split", json={"reason": "x"}, headers=tenant)
    assert r.status_code == 409, r.text
