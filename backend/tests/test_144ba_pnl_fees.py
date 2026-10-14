"""Build 144ba — processing (card) fees must count against profit. Fees post as their own
`fee` transaction; the P&L previously summed only `expense` txns, so card fees silently
overstated profit. The P&L now reports `fees` separately AND subtracts them from net profit."""
import pytest


async def _order_total(client, h, total=100):
    cid = (await client.post("/api/clients", json={"name": "C", "phone": "+1"}, headers=h)).json()["id"]
    oid = (await client.post("/api/orders", json={"client_id": cid}, headers=h)).json()["id"]
    await client.post(f"/api/orders/{oid}/lines", json={
        "line_type": "labour", "description": "Fix", "qty": 1, "unit_price": total}, headers=h)
    return oid


@pytest.mark.asyncio
async def test_card_fee_reduces_net_profit_and_shows_as_a_line(client, tenant):
    # a card account with a 2.9% processing fee
    await client.post("/api/cash/accounts", json={"name": "Card", "payment_option": "card", "fee_pct": 2.9}, headers=tenant)
    aid = [a for a in (await client.get("/api/cash/accounts", headers=tenant)).json()["items"] if a["name"] == "Card"][0]["id"]

    oid = await _order_total(client, tenant, 100)
    r = await client.post(f"/api/cash/orders/{oid}/pay", json={"method": "card", "account_id": aid, "amount": 100}, headers=tenant)
    assert r.status_code in (200, 201), r.text   # posts a 2.90 fee transaction

    p = (await client.get("/api/finance/pnl", headers=tenant)).json()
    assert "fees" in p, p
    assert p["fees"] == pytest.approx(2.90), p          # the fee is reported
    # net profit must be reduced by the fee (revenue 100 − cogs 0 − opex 0 − fees 2.90 = 97.10)
    assert p["net_profit"] == pytest.approx(97.10), p
