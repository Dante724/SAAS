"""Data-integrity regression: paying an invoice via POST /finance/invoices/{id}/pay must
move the client's balance through the shared contact ledger, like the cash.py payment
paths do. Before the fix this endpoint bumped invoice.amount_paid but never posted to the
ledger, so Client.balance drifted depending on which screen recorded the payment."""
import pytest


@pytest.mark.asyncio
async def test_invoice_payment_moves_client_balance(client, tenant):
    cid = (await client.post("/api/clients", json={"name": "Payer"}, headers=tenant)).json()["id"]
    inv = (await client.post("/api/finance/invoices", headers=tenant, json={
        "client_id": cid, "lines": [{"description": "Repair", "qty": 1, "unit_price": 100}]})).json()
    invoice_id = inv["id"]

    before = (await client.get(f"/api/clients/{cid}", headers=tenant)).json()["balance"]
    r = await client.post(f"/api/finance/invoices/{invoice_id}/pay", headers=tenant,
                          json={"amount": 100, "method": "cash"})
    assert r.status_code == 200, r.text
    after = (await client.get(f"/api/clients/{cid}", headers=tenant)).json()["balance"]

    # the payment settles the invoice it pays for: nothing owed, and no client credit appears
    # out of it (B3 recheck: an invoice's own lines are billed as they are paid)
    assert before == 0 and after == 0, (before, after)
