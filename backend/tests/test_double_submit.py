"""Double clicks and two people on one job must move stock and money once (QA multi-shop run,
31 Oct 2026). Each race is replayed deterministically: session A reads the record, session B
does the same action and commits, then A acts on its stale copy — exactly what two requests
in flight at once do."""
import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from backend.models import Order, StockMovement
from backend.tests import kit_helpers as K

P = {"line_type": "part", "kind": "product", "description": "Part", "unit_price": 20}


def _other(db):
    return async_sessionmaker(db.bind, expire_on_commit=False)


async def _estimate_with_part(client, h, qty=2, stock=10):
    await client.get("/api/statuses?is_estimate=true", headers=h)   # seed estimate statuses
    loc = await K.loc(client, h)
    pid = await K.part(client, h, loc, "Screen", 50, 10, qty=stock)
    oid, cid = await K.job(client, h, loc, estimate=True)
    await K.add_line(client, h, oid, ref_id=pid, qty=qty, **P)
    return oid, pid, cid


async def _uses(db, oid):
    db.expire_all()
    return (await db.execute(select(func.count()).select_from(StockMovement).where(
        StockMovement.ref_id == oid, StockMovement.movement_type == "use"))).scalar_one()


# ── 1. estimate → work order converts once ─────────────────────────────────────────────────────
class _Emp:
    id = None

    def __init__(self, org_id):
        self.org_id = org_id


@pytest.mark.asyncio
async def test_convert_twice_from_stale_copies_converts_once(client, tenant, db):
    from backend.routers.orders import _convert_estimate
    oid, pid, _ = await _estimate_with_part(client, tenant)
    stale = (await db.execute(select(Order).where(Order.id == oid))).scalar_one()   # A: still an estimate
    async with _other(db)() as b:
        est = (await b.execute(select(Order).where(Order.id == oid))).scalar_one()
        assert await _convert_estimate(b, _Emp(est.org_id), est) == oid
    number = (await client.get(f"/api/orders/{oid}", headers=tenant)).json()["order_number"]
    assert await _convert_estimate(db, _Emp(stale.org_id), stale) is None          # A lost the race
    await db.rollback()
    assert await K.stock(client, tenant, pid) == 8                                 # deducted once
    assert await _uses(db, oid) == 1
    assert (await client.get(f"/api/orders/{oid}", headers=tenant)).json()["order_number"] == number
    nxt = await client.post("/api/orders", headers=tenant, json={
        "client_id": None, "location_id": await K.loc(client, tenant), "complaint": "x", "lines": []})
    assert int(nxt.json()["order_number"][3:]) == int(number[3:]) + 1             # no WO number burned


@pytest.mark.asyncio
async def test_convert_endpoint_on_already_converted_is_rejected(client, tenant):
    oid, pid, _ = await _estimate_with_part(client, tenant)
    assert (await client.post(f"/api/orders/{oid}/convert", headers=tenant)).status_code == 200
    r = await client.post(f"/api/orders/{oid}/convert", headers=tenant)
    assert r.status_code in (400, 409), r.text
    assert await K.stock(client, tenant, pid) == 8


@pytest.mark.asyncio
async def test_double_customer_accept_converts_once(client, tenant, db):
    from backend.routers.public import _decide_estimate
    oid, pid, _ = await _estimate_with_part(client, tenant)
    stale = (await db.execute(select(Order).where(Order.id == oid))).scalar_one()
    async with _other(db)() as b:
        o = (await b.execute(select(Order).where(Order.id == oid))).scalar_one()
        assert await _decide_estimate(b, o, "accept", None, "Bob") == oid
        await b.commit()
    with pytest.raises(HTTPException) as e:                 # second tap: refused, nothing moves
        await _decide_estimate(db, stale, "accept", None, "Bob")
    assert e.value.status_code == 409
    await db.rollback()
    assert await K.stock(client, tenant, pid) == 8
    assert await _uses(db, oid) == 1
    o = await K.order(client, tenant, oid)
    assert o["is_estimate"] is False


@pytest.mark.asyncio
async def test_accept_after_staff_convert_does_not_convert_again(client, tenant, db):
    from backend.routers.public import _decide_estimate
    oid, pid, _ = await _estimate_with_part(client, tenant)
    stale = (await db.execute(select(Order).where(Order.id == oid))).scalar_one()
    assert (await client.post(f"/api/orders/{oid}/convert", headers=tenant)).status_code == 200
    with pytest.raises(HTTPException) as e:
        await _decide_estimate(db, stale, "accept", None, "Bob")
    assert e.value.status_code == 409
    await db.rollback()
    assert await K.stock(client, tenant, pid) == 8
    assert await _uses(db, oid) == 1


@pytest.mark.asyncio
async def test_declined_estimate_cannot_be_accepted_later(client, tenant):
    oid, pid, _ = await _estimate_with_part(client, tenant)
    tok = (await K.order(client, tenant, oid))["public_token"]
    r = await client.post(f"/api/public/track/{tok}/respond", json={"decision": "decline", "name": "Bob"})
    assert r.status_code == 200, r.text
    r = await client.post(f"/api/public/track/{tok}/respond", json={"decision": "accept", "name": "Bob"})
    assert r.status_code == 409, r.text
    o = await K.order(client, tenant, oid)
    assert o["is_estimate"] is True and await K.stock(client, tenant, pid) == 10


# ── 2 + 3 + 12. approved recommendations ───────────────────────────────────────────────────────
async def _wo_with_product_rec(client, h, stock=10, qty=2, cost=10):
    loc = await K.loc(client, h)
    pid = await K.part(client, h, loc, "Battery", 50, cost, qty=stock)
    oid, _ = await K.job(client, h, loc)
    r = await client.post(f"/api/orders/{oid}/recommendations", headers=h, json={
        "title": "Battery", "kind": "product", "ref_id": pid, "qty": qty, "unit_price": 50})
    assert r.status_code == 201, r.text
    await client.post(f"/api/orders/{oid}/recommendations/publish-all", headers=h, json={})
    return oid, pid, r.json()["id"]


@pytest.mark.asyncio
async def test_approved_product_rec_takes_stock_and_cost_and_delete_returns_it(client, tenant):
    oid, pid, rid = await _wo_with_product_rec(client, tenant)
    tok = (await K.order(client, tenant, oid))["public_token"]
    r = await client.post(f"/api/public/track/{tok}/recommendations/decide", json={"approve_ids": [rid]})
    assert r.status_code == 200 and r.json()["approved"] == 1, r.text
    assert await K.stock(client, tenant, pid) == 8                  # the part left the shelf
    ln = [x for x in await K.lines(client, tenant, oid) if x["kind"] == "product"][0]
    assert ln["cost_price"] == 10                                    # profit sees the real cost
    r = await client.delete(f"/api/orders/{oid}/lines/{ln['id']}", headers=tenant)
    assert r.status_code == 200, r.text
    assert await K.stock(client, tenant, pid) == 10                 # back to where it was, no phantom


@pytest.mark.asyncio
async def test_staff_approve_for_customer_takes_stock(client, tenant):
    oid, pid, rid = await _wo_with_product_rec(client, tenant)
    r = await client.post(f"/api/orders/{oid}/recommendations/{rid}/approve-for-customer",
                          headers=tenant, json={"how": "phone"})
    assert r.status_code == 200, r.text
    assert await K.stock(client, tenant, pid) == 8


@pytest.mark.asyncio
async def test_double_decide_from_stale_copies_adds_once_and_total_matches_lines(client, tenant, db):
    from backend.routers.public import RecDecideIn, _decide_recommendations
    oid, pid, rid = await _wo_with_product_rec(client, tenant)
    await K.add_line(client, tenant, oid, line_type="labour", kind="service", description="Fit", unit_price=30, qty=1)
    stale = (await db.execute(select(Order).where(Order.id == oid))).scalar_one()
    from backend.models import Recommendation
    stale_rec = (await db.execute(select(Recommendation).where(Recommendation.id == rid))).scalar_one()   # A: "sent"
    async with _other(db)() as b:
        o = (await b.execute(select(Order).where(Order.id == oid))).scalar_one()
        assert (await _decide_recommendations(b, o, RecDecideIn(approve_ids=[rid])))["approved"] == 1
        await b.commit()
    res = await _decide_recommendations(db, stale, RecDecideIn(approve_ids=[rid]))
    await db.commit()
    assert res["approved"] == 0 and res["skipped"] == [rid] and stale_rec is not None
    o = await K.order(client, tenant, oid)
    assert [x["description"] for x in o["lines"]].count("Battery") == 1
    assert o["total"] == pytest.approx(sum(x["total"] for x in o["lines"]))
    assert await K.stock(client, tenant, pid) == 8


@pytest.mark.asyncio
async def test_customer_cannot_add_recommendations_to_a_closed_job(client, tenant):
    oid, pid, rid = await _wo_with_product_rec(client, tenant)
    sts = (await client.get("/api/statuses?is_estimate=false", headers=tenant)).json()
    sts = sts if isinstance(sts, list) else sts.get("items", [])
    closed = next(s["key"] for s in sts if s.get("status_group") == "closed")
    r = await client.patch(f"/api/orders/{oid}/status", headers=tenant, json={"status": closed, "force": True})
    assert r.status_code == 200, r.text
    tok = (await K.order(client, tenant, oid))["public_token"]
    r = await client.post(f"/api/public/track/{tok}/recommendations/decide", json={"approve_ids": [rid]})
    assert r.status_code == 409, r.text
    assert await K.stock(client, tenant, pid) == 10


# ── 4. payments: Idempotency-Key on every desktop money path ───────────────────────────────────
async def _till(client, h):
    return (await client.post("/api/cash/accounts", json={"name": "Till", "payment_option": "cash"},
                              headers=h)).json()["id"]


async def _balance(client, h, aid):
    accs = (await client.get("/api/cash/accounts", headers=h)).json()
    accs = accs if isinstance(accs, list) else accs.get("items", [])
    return float(next(a for a in accs if a["id"] == aid)["balance"])


async def _priced_job(client, h, total=100):
    cid = (await client.post("/api/clients", json={"name": "C", "phone": "+15559"}, headers=h)).json()["id"]
    oid = (await client.post("/api/orders", json={"client_id": cid}, headers=h)).json()["id"]
    await client.post(f"/api/orders/{oid}/lines", json={
        "line_type": "labour", "description": "Fix", "qty": 1, "unit_price": total}, headers=h)
    return cid, oid


@pytest.mark.asyncio
async def test_prepay_double_click_with_same_key_posts_once(client, tenant):
    _cid, oid = await _priced_job(client, tenant)
    till = await _till(client, tenant)
    h = {**tenant, "Idempotency-Key": "prepay-1"}
    for _ in range(2):
        r = await client.post(f"/api/cash/orders/{oid}/prepay", headers=h, json={"amount": 30, "account_id": till})
        assert r.status_code == 201, r.text
        assert r.json()["prepaid"] == 30
    assert await _balance(client, tenant, till) == 30
    o = await K.order(client, tenant, oid)
    assert float(o.get("prepayment_total") or 30) == 30


@pytest.mark.asyncio
async def test_invoice_payment_double_click_with_same_key_posts_once(client, tenant):
    cid, oid = await _priced_job(client, tenant)
    till = await _till(client, tenant)
    inv = await K.invoice(client, tenant, oid, cid)
    h = {**tenant, "Idempotency-Key": "inv-pay-1"}
    for _ in range(2):
        r = await client.post(f"/api/finance/invoices/{inv}/pay", headers=h,
                              json={"amount": 40, "method": "cash", "account_id": till})
        assert r.status_code == 200, r.text
        assert r.json()["amount_paid"] == 40
    assert await _balance(client, tenant, till) == 40


@pytest.mark.asyncio
async def test_parked_sale_paid_from_stale_copy_posts_once(client, tenant, db):
    from backend.models import Employee, Sale
    from backend.routers.sales import SalePayIn, _pay_sale
    till = await _till(client, tenant)
    s = await client.post("/api/sales", headers=tenant, json={"lines": [
        {"line_type": "labour", "kind": "custom", "description": "Diagnostic", "qty": 1, "unit_price": 45}]})
    assert s.status_code == 201, s.text
    s = s.json()
    assert s["paid"] is False
    stale = (await db.execute(select(Sale).where(Sale.id == s["id"]))).scalar_one()   # A: still unpaid
    me = (await db.execute(select(Employee))).scalars().first()
    r = await client.post(f"/api/sales/{s['id']}/pay", headers=tenant, json={"account_id": till})
    assert r.status_code == 200, r.text
    with pytest.raises(HTTPException) as e:
        await _pay_sale(s["id"], SalePayIn(account_id=till), db, me)
    assert e.value.status_code == 409 and stale is not None
    await db.rollback()
    assert await _balance(client, tenant, till) == 45
    h = {**tenant, "Idempotency-Key": "sale-pay-1"}                     # a retried tap gets the answer
    s2 = (await client.post("/api/sales", headers=tenant, json={"lines": [
        {"line_type": "labour", "kind": "custom", "description": "Diagnostic", "qty": 1, "unit_price": 10}]})).json()
    for _ in range(2):
        r = await client.post(f"/api/sales/{s2['id']}/pay", headers=h, json={"account_id": till})
        assert r.status_code == 200, r.text
    assert await _balance(client, tenant, till) == 55


# ── 5 + 6. transfers and stocktakes move once ──────────────────────────────────────────────────
async def _me_emp(db):
    from backend.models import Employee
    return (await db.execute(select(Employee))).scalars().first()


@pytest.mark.asyncio
async def test_transfer_completed_twice_credits_destination_once(client, tenant, db):
    from backend.models import Location, StockTransfer
    from backend.routers.transfers import StatusIn, change_status
    from backend.tests.test_stocktake_postings_print import _loc, _me, _part, _stock
    me = await _me(client, tenant)
    loc = await _loc(client, tenant)
    dest = Location(org_id=me["org_id"], name="Branch B")
    db.add(dest)
    await db.commit()
    p = await _part(client, tenant, sku="TR-2", barcode="TR-2")
    await _stock(client, tenant, p["id"], 10)
    tid = (await client.post("/api/transfers", headers=tenant, json={
        "from_location_id": loc, "to_location_id": dest.id, "lines": [{"part_id": p["id"], "qty": 4}]})).json()["id"]
    for st in ("ready", "in_transit", "received"):
        assert (await client.patch(f"/api/transfers/{tid}/status", json={"status": st}, headers=tenant)).status_code == 200
    stale = (await db.execute(select(StockTransfer).where(StockTransfer.id == tid))).scalar_one()   # A: "received"
    emp = await _me_emp(db)
    r = await client.patch(f"/api/transfers/{tid}/status", json={"status": "completed"}, headers=tenant)
    assert r.status_code == 200, r.text
    with pytest.raises(HTTPException) as e:
        await change_status(tid, StatusIn(status="completed"), db, emp)
    assert e.value.status_code == 409 and stale is not None
    await db.rollback()
    ins = (await db.execute(select(func.coalesce(func.sum(StockMovement.qty), 0)).where(
        StockMovement.ref_id == tid, StockMovement.movement_type == "transfer_in"))).scalar_one()
    assert float(ins) == 4


@pytest.mark.asyncio
async def test_stocktake_posted_twice_applies_once(client, tenant, db):
    from backend.models import Stocktake
    from backend.routers.sales import post_stocktake
    from backend.tests.test_stocktake_postings_print import _wh
    w = await _wh(client, tenant)
    st = (await client.post("/api/stocktake", json={"warehouse_id": w["id"], "kind": "partial"}, headers=tenant)).json()
    r = (await client.post(f"/api/stocktake/{st['id']}/scan", json={
        "code": "FOUND-77", "qty": 4, "register_unknown": True}, headers=tenant)).json()
    stale = (await db.execute(select(Stocktake).where(Stocktake.id == st["id"]))).scalar_one()   # A: still a draft
    emp = await _me_emp(db)
    assert (await client.post(f"/api/stocktake/{st['id']}/post", headers=tenant)).status_code == 200
    with pytest.raises(HTTPException) as e:
        await post_stocktake(st["id"], db, emp)
    assert e.value.status_code == 409 and stale is not None
    await db.rollback()
    assert await K.stock(client, tenant, r["part_id"]) == 4


# ── 7 + 8. two people on one line ──────────────────────────────────────────────────────────────
async def _wo_line(client, h, db, qty=2):
    from backend.models import OrderLine
    loc = await K.loc(client, h)
    pid = await K.part(client, h, loc, "Screen", 50, 10, qty=10)
    oid, _ = await K.job(client, h, loc)
    lid = (await K.add_line(client, h, oid, ref_id=pid, qty=qty, **P))["line_id"]
    stale = (await db.execute(select(OrderLine).where(OrderLine.id == lid))).scalar_one()   # A's copy
    return oid, pid, lid, stale, await _me_emp(db)


@pytest.mark.asyncio
async def test_qty_and_price_edited_at_once_keep_both(client, tenant, db):
    from backend.routers.orders import LineUpdate, update_line
    oid, pid, lid, stale, emp = await _wo_line(client, tenant, db)
    r = await client.patch(f"/api/orders/{oid}/lines/{lid}", headers=tenant, json={"qty": 3})   # B: qty
    assert r.status_code == 200, r.text
    await update_line(oid, lid, LineUpdate(unit_price=30), db, emp)                             # A: price
    o = await K.order(client, tenant, oid)
    ln = next(x for x in o["lines"] if x["id"] == lid)
    assert ln["qty"] == 3 and ln["unit_price"] == 30 and ln["total"] == pytest.approx(90)
    assert o["total"] == pytest.approx(90)
    assert await K.stock(client, tenant, pid) == 7 and stale is not None


@pytest.mark.asyncio
async def test_two_qty_edits_at_once_move_stock_to_match(client, tenant, db):
    from backend.routers.orders import LineUpdate, update_line
    oid, pid, lid, stale, emp = await _wo_line(client, tenant, db)
    assert (await client.patch(f"/api/orders/{oid}/lines/{lid}", headers=tenant, json={"qty": 3})).status_code == 200
    await update_line(oid, lid, LineUpdate(qty=4), db, emp)
    ln = next(x for x in await K.lines(client, tenant, oid) if x["id"] == lid)
    assert ln["qty"] == 4
    assert await K.stock(client, tenant, pid) == 6 and stale is not None      # 10 − 4, nothing extra taken


@pytest.mark.asyncio
async def test_line_removed_twice_returns_stock_once(client, tenant, db, monkeypatch):
    """Two real DELETEs in flight: both read the line before either removes it."""
    import asyncio

    from backend.routers import orders as orders_router
    oid, pid, lid, stale, emp = await _wo_line(client, tenant, db)
    await db.rollback()
    real, arrived, go = orders_router._order_perms, [], asyncio.Event()

    async def _both_read_the_line(*a, **kw):
        arrived.append(1)
        if len(arrived) >= 2:
            go.set()
        await asyncio.wait_for(go.wait(), 5)
        return await real(*a, **kw)
    monkeypatch.setattr(orders_router, "_order_perms", _both_read_the_line)
    rs = await asyncio.gather(*[client.delete(f"/api/orders/{oid}/lines/{lid}", headers=tenant) for _ in range(2)])
    monkeypatch.setattr(orders_router, "_order_perms", real)
    assert sorted(r.status_code for r in rs)[0] == 200
    assert sorted(r.status_code for r in rs)[1] in (404, 409), [r.text for r in rs]
    assert await K.stock(client, tenant, pid) == 10 and stale is not None


@pytest.mark.asyncio
async def test_same_product_merged_at_once_counts_both(client, tenant, db):
    from backend.routers.orders import LineIn, add_line
    oid, pid, lid, stale, emp = await _wo_line(client, tenant, db)
    await K.add_line(client, tenant, oid, ref_id=pid, qty=1, **P)                  # B merges +1 → 3
    await add_line(oid, LineIn(ref_id=pid, qty=1, **P), db, emp)                   # A merges +1 on a stale 2
    ln = next(x for x in await K.lines(client, tenant, oid) if x["id"] == lid)
    assert ln["qty"] == 4
    assert await K.stock(client, tenant, pid) == 6 and stale is not None
