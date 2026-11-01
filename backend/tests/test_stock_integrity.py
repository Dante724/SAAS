"""Stock math integrity: every path that takes stock for a job (create, add, merge, qty change,
convert, receive-pending, PO auto-fill) goes through one deduction that respects reservations,
draws cost layers and serials, and books any shortfall as pending — so removing a line can never
"return" stock that was never taken. Plus manual adjust, transfers and POS serials."""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from backend.tests import kit_helpers as K

P = {"line_type": "part", "kind": "product", "description": "Part", "unit_price": 20}


async def _setup(client, h, qty=10):
    loc = await K.loc(client, h)
    pid = await K.part(client, h, loc, "Screen", 50, 10, qty=qty)
    return loc, pid


async def _product_line(client, h, oid):
    return [ln for ln in await K.lines(client, h, oid) if ln["kind"] == "product"][0]


async def _org(db, pid):
    from backend.models import Part
    return (await db.execute(select(Part.org_id).where(Part.id == pid))).scalar_one()


async def _layers(db, org, pid, loc, spec):
    """spec: [(qty, unit_cost), …] oldest first. Returns the batch ids."""
    from backend.models import StockItemBatch
    now = datetime.utcnow()
    out = []
    for i, (q, c) in enumerate(spec):
        b = StockItemBatch(org_id=org, part_id=pid, location_id=loc, unit_cost=c, qty_received=q,
                           qty_remaining=q, received_at=now - timedelta(days=len(spec) - i))
        db.add(b)
        await db.flush()
        out.append(b.id)
    await db.commit()
    return out


async def _remaining(db, ids):
    from backend.models import StockItemBatch
    db.expire_all()
    return [float((await db.execute(select(StockItemBatch.qty_remaining).where(
        StockItemBatch.id == i))).scalar_one()) for i in ids]


# ── 1. duplicate ────────────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_duplicate_job_copies_products_as_pending(client, tenant):
    loc, pid = await _setup(client, tenant)
    oid, _ = await K.job(client, tenant, loc)
    await K.add_line(client, tenant, oid, ref_id=pid, qty=3, **P)
    assert await K.stock(client, tenant, pid) == 7
    r = await client.post(f"/api/orders/{oid}/duplicate", headers=tenant, json={})
    assert r.status_code == 201, r.text
    nid = r.json()["id"]
    assert await K.stock(client, tenant, pid) == 7          # a copy never takes stock silently
    ln = await _product_line(client, tenant, nid)
    assert ln["pending_stock"] == 3                          # …so it's owed, not "held"
    r = await client.delete(f"/api/orders/{nid}/lines/{ln['id']}", headers=tenant)
    assert r.status_code == 200, r.text
    assert await K.stock(client, tenant, pid) == 7          # nothing phantom comes back


# ── 2 + 3. convert estimate → work order ─────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_convert_without_stock_row_books_pending(client, tenant):
    loc = await K.loc(client, tenant)
    pid = await K.part(client, tenant, loc, "NoStock", 50, 10, qty=0)
    oid, _ = await K.job(client, tenant, loc, estimate=True)
    await K.add_line(client, tenant, oid, ref_id=pid, qty=3, **P)
    r = await client.post(f"/api/orders/{oid}/convert", headers=tenant)
    assert r.status_code in (200, 201), r.text
    ln = await _product_line(client, tenant, oid)
    assert ln["pending_stock"] == 3
    r = await client.delete(f"/api/orders/{oid}/lines/{ln['id']}", headers=tenant)
    assert r.status_code == 200, r.text
    assert await K.stock(client, tenant, pid) == 0


@pytest.mark.asyncio
async def test_convert_respects_other_jobs_reservation(client, tenant):
    loc, pid = await _setup(client, tenant, qty=2)
    other, _ = await K.job(client, tenant, loc)
    r = await client.post("/api/inventory/reserve", headers=tenant,
                          json={"part_id": pid, "location_id": loc, "qty": 2, "order_id": other})
    assert r.status_code == 201, r.text
    est, _ = await K.job(client, tenant, loc, estimate=True)
    await K.add_line(client, tenant, est, ref_id=pid, qty=2, **P)
    r = await client.post(f"/api/orders/{est}/convert", headers=tenant)
    assert r.status_code in (200, 201), r.text
    ln = await _product_line(client, tenant, est)
    assert ln["pending_stock"] == 2                         # the other job's units stay its own
    p = (await client.get(f"/api/inventory/parts/{pid}", headers=tenant)).json()
    assert p["qty_available"] == 2 and p["qty_reserved"] == 2


@pytest.mark.asyncio
async def test_convert_draws_cost_layers(client, tenant, db):
    loc, pid = await _setup(client, tenant, qty=0)
    await client.post(f"/api/inventory/parts/{pid}/adjust", headers=tenant,
                      json={"location_id": loc, "qty": 4, "movement_type": "receive"})
    ids = await _layers(db, await _org(db, pid), pid, loc, [(2, 10), (2, 30)])
    est, _ = await K.job(client, tenant, loc, estimate=True)
    await K.add_line(client, tenant, est, ref_id=pid, qty=3, **P)
    assert await _remaining(db, ids) == [2, 2]              # an estimate touches nothing
    r = await client.post(f"/api/orders/{est}/convert", headers=tenant)
    assert r.status_code in (200, 201), r.text
    assert await _remaining(db, ids) == [0, 1]
    ln = await _product_line(client, tenant, est)
    assert ln["cost_price"] == pytest.approx((2 * 10 + 30) / 3, abs=0.01)
    assert await K.stock(client, tenant, pid) == 1


# ── 4. estimate merge ─────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_estimate_merge_does_not_deduct(client, tenant):
    loc, pid = await _setup(client, tenant)
    oid, _ = await K.job(client, tenant, loc, estimate=True)
    await K.add_line(client, tenant, oid, ref_id=pid, qty=1, **P)
    await K.add_line(client, tenant, oid, ref_id=pid, qty=2, **P)   # merges into the line
    assert await K.stock(client, tenant, pid) == 10
    assert (await _product_line(client, tenant, oid))["pending_stock"] == 0


@pytest.mark.asyncio
async def test_estimates_never_move_stock(client, tenant):
    loc, pid = await _setup(client, tenant)
    oid, _ = await K.job(client, tenant, loc, estimate=True)
    r = await K.add_line(client, tenant, oid, ref_id=pid, qty=2, **P)
    lid = r["line_id"]
    await K.add_line(client, tenant, oid, ref_id=pid, qty=1, **P)
    r = await client.patch(f"/api/orders/{oid}/lines/{lid}", headers=tenant, json={"qty": 6})
    assert r.status_code == 200, r.text
    r = await client.patch(f"/api/orders/{oid}/lines/{lid}", headers=tenant, json={"qty": 1})
    assert r.status_code == 200, r.text
    r = await client.post(f"/api/orders/{oid}/lines/{lid}/receive-stock", headers=tenant)
    assert r.status_code in (200, 400, 409)
    r = await client.delete(f"/api/orders/{oid}/lines/{lid}", headers=tenant)
    assert r.status_code == 200, r.text
    assert await K.stock(client, tenant, pid) == 10


# ── 5. receive pending ───────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_receive_pending_refused_on_archived_job(client, tenant):
    loc, pid = await _setup(client, tenant)
    oid, _ = await K.job(client, tenant, loc)
    await K.add_line(client, tenant, oid, ref_id=pid, qty=2, **P)
    assert await K.stock(client, tenant, pid) == 8
    r = await client.delete(f"/api/orders/{oid}", headers=tenant)
    assert r.status_code == 200, r.text
    assert await K.stock(client, tenant, pid) == 10
    ln = await _product_line(client, tenant, oid)
    r = await client.post(f"/api/orders/{oid}/lines/{ln['id']}/receive-stock", headers=tenant)
    assert r.status_code == 409, r.text
    assert await K.stock(client, tenant, pid) == 10


@pytest.mark.asyncio
async def test_receive_pending_respects_reservations_and_layers(client, tenant, db):
    loc, pid = await _setup(client, tenant, qty=0)
    oid, _ = await K.job(client, tenant, loc)
    await K.add_line(client, tenant, oid, ref_id=pid, qty=3, **P)
    ln = await _product_line(client, tenant, oid)
    assert ln["pending_stock"] == 3
    await client.post(f"/api/inventory/parts/{pid}/adjust", headers=tenant,
                      json={"location_id": loc, "qty": 3, "movement_type": "receive"})
    ids = await _layers(db, await _org(db, pid), pid, loc, [(3, 12)])
    other, _ = await K.job(client, tenant, loc)
    r = await client.post("/api/inventory/reserve", headers=tenant,
                          json={"part_id": pid, "location_id": loc, "qty": 1, "order_id": other})
    assert r.status_code == 201, r.text
    r = await client.post(f"/api/orders/{oid}/lines/{ln['id']}/receive-stock", headers=tenant)
    assert r.status_code == 200, r.text
    assert r.json()["pending"] == 1                         # the unit held for the other job stays
    assert await K.stock(client, tenant, pid) == 1
    assert await _remaining(db, ids) == [1]


# ── 6. manual adjust ─────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_adjust_rejects_negative_or_unknown(client, tenant):
    loc, pid = await _setup(client, tenant, qty=2)
    r = await client.post(f"/api/inventory/parts/{pid}/adjust", headers=tenant,
                          json={"location_id": loc, "qty": -5, "movement_type": "receive"})
    assert r.status_code == 422, r.text
    r = await client.post(f"/api/inventory/parts/{pid}/adjust", headers=tenant,
                          json={"location_id": loc, "qty": 0, "movement_type": "receive"})
    assert r.status_code == 422, r.text
    r = await client.post(f"/api/inventory/parts/{pid}/adjust", headers=tenant,
                          json={"location_id": loc, "qty": 1, "movement_type": "teleport"})
    assert r.status_code == 422, r.text
    assert await K.stock(client, tenant, pid) == 2


@pytest.mark.asyncio
async def test_writeoff_logs_negative_of_what_was_drawn(client, tenant, db):
    from backend.models import StockMovement
    loc, pid = await _setup(client, tenant, qty=2)
    r = await client.post(f"/api/inventory/parts/{pid}/adjust", headers=tenant,
                          json={"location_id": loc, "qty": 5, "movement_type": "writeoff"})
    assert r.status_code == 200, r.text
    assert await K.stock(client, tenant, pid) == 0
    mv = (await db.execute(select(StockMovement.qty).where(
        StockMovement.part_id == pid, StockMovement.movement_type == "writeoff"))).scalars().all()
    assert mv == [-2]
    total = sum((await db.execute(select(StockMovement.qty).where(
        StockMovement.part_id == pid))).scalars().all())
    assert total == 0                                       # movements add up to on-hand


# ── 7. transfers ──────────────────────────────────────────────────────────────────────────────
async def _transfer(client, h, loc, pid, qty=3):
    r = await client.post("/api/staff/locations", headers=h, json={"name": "Branch 2"})
    assert r.status_code in (200, 201), r.text
    loc2 = r.json()["id"]
    r = await client.post("/api/transfers", headers=h, json={
        "from_location_id": loc, "to_location_id": loc2, "lines": [{"part_id": pid, "qty": qty}]})
    assert r.status_code in (200, 201), r.text
    tid = r.json()["id"]
    r = await client.patch(f"/api/transfers/{tid}/status", headers=h, json={"status": "ready"})
    assert r.status_code == 200, r.text
    return tid, loc2


async def _at(db, pid, loc):
    from backend.services import stock_rows
    db.expire_all()
    return await stock_rows.totals(db, pid, loc)


@pytest.mark.asyncio
async def test_transfer_ready_counts_units_once(client, tenant, db):
    loc, pid = await _setup(client, tenant, qty=10)
    await _transfer(client, tenant, loc, pid)
    assert await _at(db, pid, loc) == (7, 0)
    r = await client.post("/api/inventory/reserve", headers=tenant,
                          json={"part_id": pid, "location_id": loc, "qty": 7})
    assert r.status_code == 201, r.text                    # all 7 left are reservable


@pytest.mark.asyncio
async def test_transfer_ready_then_complete_balances(client, tenant, db):
    loc, pid = await _setup(client, tenant, qty=10)
    tid, loc2 = await _transfer(client, tenant, loc, pid)
    for st in ("in_transit", "received", "completed"):
        r = await client.patch(f"/api/transfers/{tid}/status", headers=tenant, json={"status": st})
        assert r.status_code == 200, (st, r.text)
    assert await _at(db, pid, loc) == (7, 0)
    assert await _at(db, pid, loc2) == (3, 0)


@pytest.mark.asyncio
async def test_transfer_ready_then_cancel_balances(client, tenant, db):
    loc, pid = await _setup(client, tenant, qty=10)
    tid, _loc2 = await _transfer(client, tenant, loc, pid)
    r = await client.patch(f"/api/transfers/{tid}/status", headers=tenant, json={"status": "canceled"})
    assert r.status_code == 200, r.text
    assert await _at(db, pid, loc) == (10, 0)


# ── 8. cost layers on job creation ─────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_job_creation_draws_cost_layers(client, tenant, db):
    loc, pid = await _setup(client, tenant, qty=0)
    await client.post(f"/api/inventory/parts/{pid}/adjust", headers=tenant,
                      json={"location_id": loc, "qty": 3, "movement_type": "receive"})
    ids = await _layers(db, await _org(db, pid), pid, loc, [(1, 10), (2, 40)])
    cid = (await client.post("/api/clients", json={"name": "C"}, headers=tenant)).json()["id"]
    r = await client.post("/api/orders", headers=tenant, json={
        "client_id": cid, "location_id": loc, "complaint": "x",
        "lines": [{**P, "ref_id": pid, "qty": 2}]})
    assert r.status_code in (200, 201), r.text
    oid = r.json()["id"]
    assert await _remaining(db, ids) == [0, 1]
    ln = await _product_line(client, tenant, oid)
    assert ln["cost_price"] == pytest.approx(25, abs=0.01)
    assert ln["pending_stock"] == 0
    # removing the line puts the layers back
    r = await client.delete(f"/api/orders/{oid}/lines/{ln['id']}", headers=tenant)
    assert r.status_code == 200, r.text
    assert await _remaining(db, ids) == [1, 2]
    assert await K.stock(client, tenant, pid) == 3


# ── 9. qty increase keeps a weighted cost ──────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_qty_increase_weights_line_cost(client, tenant, db):
    loc, pid = await _setup(client, tenant, qty=0)
    await client.post(f"/api/inventory/parts/{pid}/adjust", headers=tenant,
                      json={"location_id": loc, "qty": 3, "movement_type": "receive"})
    await _layers(db, await _org(db, pid), pid, loc, [(1, 10), (2, 40)])
    oid, _ = await K.job(client, tenant, loc)
    lid = (await K.add_line(client, tenant, oid, ref_id=pid, qty=1, **P))["line_id"]
    assert (await _product_line(client, tenant, oid))["cost_price"] == pytest.approx(10)
    r = await client.patch(f"/api/orders/{oid}/lines/{lid}", headers=tenant, json={"qty": 3})
    assert r.status_code == 200, r.text
    assert (await _product_line(client, tenant, oid))["cost_price"] == pytest.approx(30, abs=0.01)


@pytest.mark.asyncio
async def test_qty_change_over_stock_round_trips(client, tenant):
    loc, pid = await _setup(client, tenant, qty=2)
    oid, _ = await K.job(client, tenant, loc)
    lid = (await K.add_line(client, tenant, oid, ref_id=pid, qty=1, **P))["line_id"]
    r = await client.patch(f"/api/orders/{oid}/lines/{lid}", headers=tenant, json={"qty": 5})
    assert r.status_code == 200, r.text
    ln = await _product_line(client, tenant, oid)
    assert await K.stock(client, tenant, pid) == 0 and ln["pending_stock"] == 3
    r = await client.patch(f"/api/orders/{oid}/lines/{lid}", headers=tenant, json={"qty": 1})
    assert r.status_code == 200, r.text
    assert await K.stock(client, tenant, pid) == 1


# ── archive / restore ─────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_archive_restore_keeps_counts(client, tenant):
    loc, pid = await _setup(client, tenant)
    oid, _ = await K.job(client, tenant, loc)
    await K.add_line(client, tenant, oid, ref_id=pid, qty=4, **P)
    assert await K.stock(client, tenant, pid) == 6
    assert (await client.delete(f"/api/orders/{oid}", headers=tenant)).status_code == 200
    assert await K.stock(client, tenant, pid) == 10
    r = await client.post(f"/api/orders/{oid}/restore", headers=tenant)
    assert r.status_code == 200, r.text
    assert await K.stock(client, tenant, pid) == 10
    ln = await _product_line(client, tenant, oid)
    assert ln["pending_stock"] == 4
    r = await client.post(f"/api/orders/{oid}/lines/{ln['id']}/receive-stock", headers=tenant)
    assert r.status_code == 200, r.text
    assert await K.stock(client, tenant, pid) == 6
    r = await client.delete(f"/api/orders/{oid}/lines/{ln['id']}", headers=tenant)
    assert r.status_code == 200, r.text
    assert await K.stock(client, tenant, pid) == 10


# ── 11. POS serials ───────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_pos_sale_marks_serials_sold_and_void_restores(client, tenant, db):
    from backend.models import SerializedItem
    loc, pid = await _setup(client, tenant, qty=2)
    org = await _org(db, pid)
    db.add_all([SerializedItem(org_id=org, part_id=pid, serial_number=f"SN-{i}", location_id=loc,
                               status="in_stock") for i in range(2)])
    await db.commit()

    async def statuses():
        db.expire_all()
        return sorted((await db.execute(select(SerializedItem.status).where(
            SerializedItem.part_id == pid))).scalars().all())

    r = await client.post("/api/sales", headers=tenant, json={"lines": [
        {"kind": "product", "ref_id": pid, "description": "Screen", "qty": 1, "unit_price": 50}]})
    assert r.status_code == 201, r.text
    assert await statuses() == ["in_stock", "sold"]
    r = await client.delete(f"/api/sales/{r.json()['id']}?reason=oops", headers=tenant)
    assert r.status_code == 200, r.text
    assert await statuses() == ["in_stock", "in_stock"]
