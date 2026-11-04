"""QA wave 3, section W3-C (5-shop re-run on Postgres, 1 Nov 2026): goods returns, refunds, deleting
a paid job.

1. A goods return against a job took any part, any number of times: phantom stock at cost 0 with no
   cost layer, and the job's parts cost went down again each time.
2. Refunding a deposit on an open job added a −200 line and cut the job's price.
3. Deleting a paid job refunded everything from the first payment's account (the drawer went
   negative), and restocked parts a goods return had already put back.
4. Invoices kept "paid" after refunds and a delete; deleting a posted write-off left its cost layer
   empty; the front desk (can_create_returns) couldn't take a goods return; a pay or prepay sent
   twice without an Idempotency-Key posted twice.
"""
import asyncio

import pytest
from sqlalchemy import func, select

from backend.tests import kit_helpers as K

P = {"line_type": "part", "kind": "product", "description": "Battery", "unit_price": 60}


async def _account(client, h, name, option):
    r = await client.post("/api/cash/accounts", json={"name": name, "payment_option": option}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _balance(client, h, aid):
    return next(a for a in (await client.get("/api/cash/accounts", headers=h)).json()["items"]
                if a["id"] == aid)["balance"]


async def _stocked_part(client, h, loc, name="Battery", qty=5, cost=10):
    """A product received on a posting, so it has a cost layer."""
    import uuid
    pid = (await client.post("/api/inventory/parts", json={
        "name": name, "sku": f"W3C-{uuid.uuid4().hex[:6]}", "sell_price": 60, "cost_price": cost},
        headers=h)).json()["id"]
    r = await client.post("/api/inventory/postings", json={
        "location_id": loc, "lines": [{"part_id": pid, "qty": qty, "purchase_price": cost}]}, headers=h)
    assert r.status_code in (200, 201), r.text
    return pid


async def _layers(db, pid):
    from backend.models import StockItemBatch
    db.expire_all()
    return float((await db.execute(select(func.coalesce(func.sum(StockItemBatch.qty_remaining), 0)).where(
        StockItemBatch.part_id == pid))).scalar() or 0)


async def _on_hand(db, pid, loc):
    from backend.services import stock_rows
    db.expire_all()
    return float((await stock_rows.totals(db, pid, loc))[0])


async def _job_with_part(client, h, qty=1):
    loc = await K.loc(client, h)
    pid = await _stocked_part(client, h, loc)
    oid, cid = await K.job(client, h, loc)
    await K.add_line(client, h, oid, ref_id=pid, qty=qty, **P)
    return loc, pid, oid, cid


async def _parts_cost(db, oid):
    from backend.services.revenue import job_figures
    db.expire_all()
    return (await job_figures(db, [oid]))[oid]["parts_cost"]


# ── 1. a goods return against a job takes only what the job used, once ─────────────────────────
@pytest.mark.asyncio
async def test_returning_a_part_the_job_never_used_is_refused(client, tenant, db):
    loc, pid, oid, cid = await _job_with_part(client, tenant)
    other = await _stocked_part(client, tenant, loc, name="Fan")
    r = await client.post("/api/returns", json={"order_id": oid, "client_id": cid,
                                                "lines": [{"part_id": other, "qty": 1}]}, headers=tenant)
    assert r.status_code == 409, r.text
    assert "job" in r.json()["detail"].lower()
    assert await _on_hand(db, other, loc) == 5


@pytest.mark.asyncio
async def test_a_job_return_is_capped_at_what_the_job_used(client, tenant, db):
    loc, pid, oid, cid = await _job_with_part(client, tenant)
    assert await _on_hand(db, pid, loc) == 4
    body = {"order_id": oid, "client_id": cid, "lines": [{"part_id": pid, "qty": 2}]}
    assert (await client.post("/api/returns", json=body, headers=tenant)).status_code == 409   # only 1 used
    body["lines"][0]["qty"] = 1
    assert (await client.post("/api/returns", json=body, headers=tenant)).status_code == 201
    r = await client.post("/api/returns", json={**body, "reason": "again"}, headers=tenant)
    assert r.status_code == 409, r.text                               # already back
    assert await _on_hand(db, pid, loc) == 5
    assert await _layers(db, pid) == 5                                # back on its cost layer
    assert await _parts_cost(db, oid) == pytest.approx(0)             # taken off the job once


@pytest.mark.asyncio
async def test_a_double_clicked_job_return_restocks_once(client, tenant, db):
    loc, pid, oid, cid = await _job_with_part(client, tenant)
    body = {"order_id": oid, "client_id": cid, "lines": [{"part_id": pid, "qty": 1}]}
    rs = await asyncio.gather(*[client.post("/api/returns", json=body, headers=tenant) for _ in range(2)])
    assert sorted(r.status_code for r in rs) == [201, 409], [r.text for r in rs]
    assert await _on_hand(db, pid, loc) == 5
    assert await _layers(db, pid) == 5


@pytest.mark.asyncio
async def test_a_partial_job_return_leaves_the_rest_returnable(client, tenant, db):
    loc, pid, oid, cid = await _job_with_part(client, tenant, qty=2)
    body = {"order_id": oid, "client_id": cid, "lines": [{"part_id": pid, "qty": 1}]}
    assert (await client.post("/api/returns", json=body, headers=tenant)).status_code == 201
    assert (await client.post("/api/returns", json={**body, "reason": "second"}, headers=tenant)).status_code == 201
    assert await _on_hand(db, pid, loc) == 5
    assert await _parts_cost(db, oid) == pytest.approx(0)


# ── 2. a deposit refund on an open job leaves its price alone ──────────────────────────────────
async def _deposit_job(client, h, price=650, deposit=200):
    loc = await K.loc(client, h)
    oid, cid = await K.job(client, h, loc)
    await K.add_line(client, h, oid, line_type="labour", kind="service", description="Full service",
                     unit_price=price)
    till = await _account(client, h, "Cash drawer", "cash")
    r = await client.post(f"/api/cash/orders/{oid}/prepay", json={"amount": deposit, "account_id": till},
                          headers=h)
    assert r.status_code == 201, r.text
    return oid, cid, till


@pytest.mark.asyncio
async def test_refunding_a_deposit_on_an_open_job_keeps_the_price(client, tenant, db):
    from backend.services.order_money import balance_due, net_paid
    oid, _, till = await _deposit_job(client, tenant)
    r = await client.post("/api/returns/refunds", json={"order_id": oid, "amount": 200, "account_id": till,
                                                        "reason": "pay at pickup instead"}, headers=tenant)
    assert r.status_code == 201, r.text
    o = await K.order(client, tenant, oid)
    assert o["total"] == pytest.approx(650)
    assert not [ln for ln in o["lines"] if ln["total"] < 0]
    from backend.models import Order
    db.expire_all()
    order = (await db.execute(select(Order).where(Order.id == oid))).scalar_one()
    assert await net_paid(db, oid) == 0 and await balance_due(db, order) == pytest.approx(650)
    assert await _balance(client, tenant, till) == pytest.approx(0)


@pytest.mark.asyncio
async def test_the_cash_refund_path_gives_back_a_deposit(client, tenant, db):
    oid, _, till = await _deposit_job(client, tenant)
    r = await client.post(f"/api/cash/orders/{oid}/refund", json={"amount": 250, "account_id": till},
                          headers=tenant)
    assert r.status_code == 400, r.text                               # only 200 was put down
    r = await client.post(f"/api/cash/orders/{oid}/refund", json={"amount": 200, "account_id": till},
                          headers=tenant)
    assert r.status_code == 201, r.text
    assert (await K.order(client, tenant, oid))["total"] == pytest.approx(650)
    ctx = (await client.get(f"/api/cash/orders/{oid}/payment-context", headers=tenant)).json()
    assert ctx["already_paid"] == 0 and ctx["remaining"] == pytest.approx(650)
    assert await _balance(client, tenant, till) == pytest.approx(0)
    # it's a refund document like any other: listed, and it can be deleted
    refunds = (await client.get("/api/returns/refunds", headers=tenant)).json()["items"]
    assert [x["order_id"] for x in refunds] == [oid]


@pytest.mark.asyncio
async def test_a_deposit_refund_takes_the_money_off_the_invoice(client, tenant, db):
    oid, cid, till = await _deposit_job(client, tenant)
    r = await client.post("/api/finance/invoices", json={"order_id": oid}, headers=tenant)
    assert r.status_code in (200, 201), r.text
    iid = r.json()["id"]
    inv = (await client.get(f"/api/finance/invoices/{iid}", headers=tenant)).json()
    assert float(inv["amount_paid"]) == 200
    r = await client.post(f"/api/cash/orders/{oid}/refund", json={"amount": 200, "account_id": till},
                          headers=tenant)
    assert r.status_code == 201, r.text
    inv = (await client.get(f"/api/finance/invoices/{iid}", headers=tenant)).json()
    assert float(inv["amount_paid"]) == 0 and inv["status"] in ("draft", "issued")


# ── 3. deleting a paid job: money back per account, parts back once ────────────────────────────
@pytest.mark.asyncio
async def test_deleting_a_paid_job_refunds_each_payment_to_its_own_account(client, tenant, db):
    loc = await K.loc(client, tenant)
    oid, _ = await K.job(client, tenant, loc)
    await K.add_line(client, tenant, oid, line_type="labour", kind="service", description="Fix", unit_price=300)
    cash = await _account(client, tenant, "Cash drawer", "cash")
    card = await _account(client, tenant, "Card", "card")
    for acc, method, amount in ((cash, "cash", 20), (card, "card", 200), (cash, "cash", 30), (card, "card", 50)):
        r = await client.post(f"/api/cash/orders/{oid}/pay", json={"method": method, "account_id": acc,
                                                                   "amount": amount}, headers=tenant)
        assert r.status_code in (200, 201), r.text
    r = await client.delete(f"/api/orders/{oid}?reason=duplicate", headers=tenant)
    assert r.status_code == 200, r.text
    assert r.json()["refunded"] == pytest.approx(300)
    assert await _balance(client, tenant, cash) == pytest.approx(0)
    assert await _balance(client, tenant, card) == pytest.approx(0)


@pytest.mark.asyncio
async def test_deleting_a_paid_job_never_quietly_empties_the_drawer(client, tenant, db):
    loc = await K.loc(client, tenant)
    oid, _ = await K.job(client, tenant, loc)
    await K.add_line(client, tenant, oid, line_type="labour", kind="service", description="Fix", unit_price=100)
    cash = await _account(client, tenant, "Cash drawer", "cash")
    safe = await _account(client, tenant, "Safe", "cashless")
    await client.post(f"/api/cash/orders/{oid}/pay", json={"method": "cash", "account_id": cash, "amount": 100},
                      headers=tenant)
    r = await client.post("/api/cash/transfer", json={"from_account_id": cash, "to_account_id": safe,
                                                      "amount": 80}, headers=tenant)
    assert r.status_code == 201, r.text
    r = await client.delete(f"/api/orders/{oid}?reason=duplicate", headers=tenant)
    assert r.status_code == 409, r.text
    assert "drawer" in r.json()["detail"].lower()
    assert await _balance(client, tenant, cash) == pytest.approx(20)        # nothing moved
    assert not (await K.order(client, tenant, oid)).get("archived")


@pytest.mark.asyncio
async def test_a_goods_return_then_deleting_the_job_restocks_once(client, tenant, db):
    loc, pid, oid, cid = await _job_with_part(client, tenant)
    till = await _account(client, tenant, "Cash drawer", "cash")
    await client.post(f"/api/cash/orders/{oid}/pay", json={"method": "cash", "account_id": till, "amount": 60},
                      headers=tenant)
    r = await client.post("/api/returns", json={"order_id": oid, "client_id": cid,
                                                "lines": [{"part_id": pid, "qty": 1, "restock": True}]},
                          headers=tenant)
    assert r.status_code == 201, r.text
    assert await _on_hand(db, pid, loc) == 5
    r = await client.delete(f"/api/orders/{oid}?reason=customer left", headers=tenant)
    assert r.status_code == 200, r.text
    assert await _on_hand(db, pid, loc) == 5
    assert await _layers(db, pid) == 5


@pytest.mark.asyncio
async def test_deleting_a_paid_job_cancels_its_invoice(client, tenant, db):
    loc = await K.loc(client, tenant)
    oid, _ = await K.job(client, tenant, loc)
    await K.add_line(client, tenant, oid, line_type="labour", kind="service", description="Fix", unit_price=100)
    cash = await _account(client, tenant, "Cash drawer", "cash")
    await client.post(f"/api/cash/orders/{oid}/pay", json={"method": "cash", "account_id": cash, "amount": 100},
                      headers=tenant)
    inv = (await client.get(f"/api/cash/orders/{oid}/invoices", headers=tenant)).json()["invoices"][0]
    assert inv["status"] == "paid"
    assert (await client.delete(f"/api/orders/{oid}?reason=duplicate", headers=tenant)).status_code == 200
    inv = (await client.get(f"/api/finance/invoices/{inv['id']}", headers=tenant)).json()
    assert float(inv["amount_paid"]) == 0
    assert inv["status"] in ("void", "voided", "cancelled", "canceled")


# ── 4. the rest ────────────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_deleting_a_write_off_puts_its_cost_layer_back(client, tenant, db):
    loc = await K.loc(client, tenant)
    pid = await _stocked_part(client, tenant, loc, name="SSD")
    r = await client.post("/api/stock-docs/write-offs", json={
        "location_id": loc, "reason": "damaged", "lines": [{"part_id": pid, "qty": 1}]}, headers=tenant)
    assert r.status_code == 201, r.text
    assert await _on_hand(db, pid, loc) == 4 and await _layers(db, pid) == 4
    assert (await client.delete(f"/api/stock-docs/write-offs/{r.json()['id']}", headers=tenant)).status_code == 200
    assert await _on_hand(db, pid, loc) == 5
    assert await _layers(db, pid) == 5


@pytest.mark.asyncio
async def test_front_desk_with_returns_permission_takes_a_goods_return(client, tenant, db):
    from backend.tests.test_qa_f1_jobs import _staff
    loc, pid, oid, cid = await _job_with_part(client, tenant)
    desk = await _staff(client, tenant, {"can_create_returns": True, "can_manage_inventory": False})
    r = await client.post("/api/returns", json={"order_id": oid, "client_id": cid,
                                                "lines": [{"part_id": pid, "qty": 1}]}, headers=desk)
    assert r.status_code == 201, r.text
    nobody = await _staff(client, tenant, {"can_create_returns": False, "can_manage_inventory": False})
    r = await client.post("/api/returns", json={"order_id": oid, "client_id": cid,
                                                "lines": [{"part_id": pid, "qty": 1}]}, headers=nobody)
    assert r.status_code == 403, r.text


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["pay", "prepay"])
async def test_the_same_payment_sent_twice_without_a_key_posts_once(client, tenant, db, path):
    loc = await K.loc(client, tenant)
    oid, _ = await K.job(client, tenant, loc)
    await K.add_line(client, tenant, oid, line_type="labour", kind="service", description="Fix", unit_price=300)
    cash = await _account(client, tenant, "Cash drawer", "cash")
    body = {"method": "cash", "account_id": cash, "amount": 100}
    first = await client.post(f"/api/cash/orders/{oid}/{path}", json=body, headers=tenant)
    assert first.status_code in (200, 201), first.text
    again = await client.post(f"/api/cash/orders/{oid}/{path}", json=body, headers=tenant)
    assert again.status_code == 409, again.text
    assert await _balance(client, tenant, cash) == pytest.approx(100)
    # a deliberate second payment carries its own key and goes through
    keyed = await client.post(f"/api/cash/orders/{oid}/{path}", json=body,
                              headers={**tenant, "Idempotency-Key": f"second-{path}"})
    assert keyed.status_code in (200, 201), keyed.text
    assert await _balance(client, tenant, cash) == pytest.approx(200)
