"""Final QA gate (1 Nov 2026, Postgres), on roadmap/orderry-parity after the main sync.

F-3: two till returns of the same item sent at once (a double click, no Idempotency-Key) refunded
     and restocked twice when the sale had more of it to return.
C-6: a fully returned till sale still showed its revenue on Total profit.
"""
import asyncio

import pytest

from backend.tests.test_b5_pos_quote import _balance, _product, _sales_tax, _stock, _till
from backend.tests.test_report_figures_agree import _total_profit


async def _sale(client, H, pid, till, qty, price):
    r = await client.post("/api/sales", json={"account_id": till, "lines": [
        {"kind": "product", "ref_id": pid, "description": "Charger", "qty": qty, "unit_price": price}]}, headers=H)
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    line = (await client.get(f"/api/sales/{sid}", headers=H)).json()["lines"][0]
    return sid, line


@pytest.mark.asyncio
async def test_double_clicked_partial_return_refunds_and_restocks_once(client, tenant, db):
    till = await _till(client, tenant)
    pid, loc = await _product(client, tenant, db, qty=10, price=30)
    sid, line = await _sale(client, tenant, pid, till, 2, 30)
    assert await _stock(db, pid, loc) == 8
    before = await _balance(client, tenant, till)
    body = {"lines": [{"line_id": line["id"], "qty": 1}], "account_id": till, "reason": "double click"}
    rs = await asyncio.gather(*[client.post(f"/api/sales/{sid}/returns", json=body, headers=tenant) for _ in range(2)])
    codes = sorted(r.status_code for r in rs)
    assert codes == [201, 409], [r.text for r in rs]
    assert await _stock(db, pid, loc) == 9
    assert await _balance(client, tenant, till) == pytest.approx(before - 30)
    # the other unit can still come back afterwards: it's a new return, not the same click
    left = (await client.get(f"/api/sales/{sid}", headers=tenant)).json()["lines"][0]
    assert left["returnable"] == 1


@pytest.mark.asyncio
async def test_a_second_return_after_the_first_is_not_mistaken_for_a_double_click(client, tenant, db):
    till = await _till(client, tenant)
    pid, loc = await _product(client, tenant, db, qty=10, price=30)
    sid, line = await _sale(client, tenant, pid, till, 2, 30)
    body = {"lines": [{"line_id": line["id"], "qty": 1}], "account_id": till}
    assert (await client.post(f"/api/sales/{sid}/returns", json=body, headers=tenant)).status_code == 201
    r = await client.post(f"/api/sales/{sid}/returns", json={**body, "reason": "second unit later"},
                          headers={**tenant, "Idempotency-Key": "second-unit"})
    assert r.status_code == 201, r.text           # a deliberate second return (its own key) goes through
    assert await _stock(db, pid, loc) == 10


@pytest.mark.asyncio
async def test_a_fully_returned_sale_earns_nothing_on_total_profit(client, tenant, db):
    await _sales_tax(client, tenant, 10)
    till = await _till(client, tenant)
    pid, _loc = await _product(client, tenant, db, qty=5, price=50)
    sid, line = await _sale(client, tenant, pid, till, 1, 50)
    sale = (await client.get(f"/api/sales/{sid}", headers=tenant)).json()
    assert sale["total"] == pytest.approx(55)                    # 50 + 5 tax
    tp = await _total_profit(client, tenant)
    assert tp["totals"]["revenue"] == pytest.approx(50) and tp["totals"]["tax"] == pytest.approx(5)
    r = await client.post(f"/api/sales/{sid}/returns", json={"lines": [{"line_id": line["id"], "qty": 1}],
                                                             "account_id": till}, headers=tenant)
    assert r.status_code == 201, r.text
    tp = await _total_profit(client, tenant)
    assert tp["totals"]["revenue"] == pytest.approx(0), (tp["totals"], tp["buckets"])
    assert tp["totals"]["tax"] == pytest.approx(0)
    assert tp["totals"]["cogs"] == pytest.approx(0)             # the charger is back on the shelf
    sales = tp["buckets"]["sales"]                               # the sale's own line nets the return
    assert sales["revenue"] == pytest.approx(0) and sales["tax"] == pytest.approx(0), tp["buckets"]
    pnl = (await client.get("/api/finance/pnl", headers=tenant)).json()
    assert pnl["revenue"] == pytest.approx(0) and pnl["tax_collected"] == pytest.approx(0)
