"""Re-review money N1 (H4): a cashbox-restricted role must be refused BEFORE the card provider is
called. Before the fix a refund went back to the customer's card at Stripe, then post_transaction
refused the account (403) and nothing was booked."""
import pytest

from backend.tests.test_opf_card_readers import _card_account, _charge, _order, _reader, stripe  # noqa: F401
from backend.tests.test_p1_money_audit import _acct, _staff


@pytest.fixture(autouse=True)
def _readers_on(card_readers_on):
    """Card-reader routes: run with the feature on."""


def _calls(fake, path):
    return [c for c in fake.calls if c[1] == path]


@pytest.mark.asyncio
async def test_restricted_role_refund_refused_before_the_provider(client, tenant, stripe):  # noqa: F811
    card = await _card_account(client, tenant)
    till = await _acct(client, tenant, "Front till")
    t = await _reader(client, tenant)
    oid = await _order(client, tenant, 100)
    ch = (await _charge(client, tenant, t["id"], order_id=oid, account_id=card)).json()
    await client.post(f"/api/payments/terminal/charges/{ch['id']}/simulate", json={"outcome": "approve"},
                      headers=tenant)
    s = await _staff(client, tenant, account_ids=[till])
    r = await client.post(f"/api/payments/terminal/charges/{ch['id']}/refund", json={"amount": 10}, headers=s)
    assert r.status_code == 403, r.text
    assert _calls(stripe, "/v1/refunds") == []          # the card was never refunded
    got = (await client.get(f"/api/payments/terminal/charges/{ch['id']}", headers=tenant)).json()
    assert got["refunded_amount"] == 0


@pytest.mark.asyncio
async def test_restricted_role_cannot_start_a_charge_into_another_account(client, tenant, stripe):  # noqa: F811
    card = await _card_account(client, tenant)
    till = await _acct(client, tenant, "Front till")
    t = await _reader(client, tenant)
    oid = await _order(client, tenant, 100)
    s = await _staff(client, tenant, account_ids=[till])
    r = await _charge(client, s, t["id"], order_id=oid, account_id=card)
    assert r.status_code == 403, r.text
    # no account named: the default card account isn't theirs, so nothing is picked or sent
    r = await _charge(client, s, t["id"], order_id=oid)
    assert r.status_code in (400, 403), r.text
    assert _calls(stripe, "/v1/payment_intents") == []


@pytest.mark.asyncio
async def test_restricted_role_poll_does_not_settle_into_another_account(client, tenant, stripe):  # noqa: F811
    card = await _card_account(client, tenant)
    till = await _acct(client, tenant, "Front till")
    t = await _reader(client, tenant)
    oid = await _order(client, tenant, 100)
    ch = (await _charge(client, tenant, t["id"], order_id=oid, account_id=card)).json()
    s = await _staff(client, tenant, account_ids=[till])
    before = len(stripe.calls)
    r = await client.get(f"/api/payments/terminal/charges/{ch['id']}", headers=s)
    assert r.status_code == 200, r.text            # read only: no 403 after the provider was asked
    assert len(stripe.calls) == before             # and the provider wasn't polled for it
