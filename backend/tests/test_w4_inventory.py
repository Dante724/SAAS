"""Wave 4, group E (inventory follow-ups from #207, #217, #225).

- INV-02: a job line keeps a journal of the cost layers it drew (order_lines.stock_draws), so giving
  the units back puts them on exactly those layers. Before, a restore refilled the newest layer with
  room, so a line that drew the cheap layer could refill the dear one and shift valuation.
- INV-06: a walk-in return can name the sale the goods came from (`sale_id`). It is then bounded by
  that sale: only products on it, never more than sold less what already came back (through the till
  or an earlier walk-in return), and the units go back onto the sale's own cost layers.
- stock_rows.totals / free count only the warehouses the person on the request can reach, the same
  rows `stock_rows.draw` may take from (re-review N3 limited the draw but not the availability).
- A stocktake that leaves fewer units on the shelf than are reserved trims the holds to what is
  there (newest holds first) and says which jobs lost units, instead of leaving reserved > on hand.
"""
import pytest
from sqlalchemy import func, select

from backend.tests import kit_helpers as K
from backend.tests.test_qa_f1_jobs import _staff
from backend.tests.test_qa_f2_stock import _ledger_matches, _on_hand

pytestmark = pytest.mark.asyncio

P = {"line_type": "part", "kind": "product", "description": "Screen", "unit_price": 99}


async def _receive(client, h, pid, loc, qty, cost):
    r = await client.post(f"/api/inventory/parts/{pid}/adjust", headers=h, json={
        "location_id": loc, "qty": qty, "movement_type": "receive", "unit_cost": cost})
    assert r.status_code == 200, r.text


async def _layers(db, pid):
    """[(unit_cost, qty_remaining)] oldest first."""
    from backend.models import StockItemBatch
    db.expire_all()
    return [(float(c), float(q)) for c, q in (await db.execute(
        select(StockItemBatch.unit_cost, StockItemBatch.qty_remaining).where(StockItemBatch.part_id == pid)
        .order_by(StockItemBatch.received_at, StockItemBatch.id))).all()]


# ── INV-02: per-line layer journal ──────────────────────────────────────────────────────────────
async def _job_line(client, tenant, db, loc, pid, qty):
    from backend.models import OrderLine
    oid, _ = await K.job(client, tenant, loc)
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    line = OrderLine(org_id=me["org_id"], order_id=oid, line_type="part", kind="product", ref_id=pid,
                     description="Screen", qty=qty, unit_price=99, cost_price=0)
    db.add(line)
    await db.flush()
    return me["org_id"], oid, line


async def test_line_journal_restores_exactly_the_layers_it_drew(client, tenant, db):
    """Layers 10 x2 (old) and 30 x2 (new). The job takes 2 (both cheap units), a sale then takes one
    dear unit. Giving the job's 2 back must refill the cheap layer: newest-with-room put one on the
    dear layer, so the shelf was valued 10+30+30+30 instead of 10+10+30."""
    from backend.services import stock_consume
    loc = await K.loc(client, tenant)
    pid = await K.part(client, tenant, loc, "Screen", 99, 10, qty=0)
    await _receive(client, tenant, pid, loc, 2, 10)
    await _receive(client, tenant, pid, loc, 2, 30)
    org, oid, line = await _job_line(client, tenant, db, loc, pid, 2)
    taken = await stock_consume.consume_for_line(db, org_id=org, order_id=oid, line=line, location_id=loc, qty=2)
    assert taken == 2 and float(line.cost_price) == 10
    assert [d["qty"] for d in line.stock_draws["layers"]] == [2]
    line_id = line.id
    await db.commit()
    assert await _layers(db, pid) == [(10, 0), (30, 2)]
    # a counter sale takes one of the dear units
    from backend.models import StockItemBatch
    dear = (await db.execute(select(StockItemBatch).where(StockItemBatch.part_id == pid,
                                                          StockItemBatch.unit_cost == 30))).scalar_one()
    dear.qty_remaining = 1
    await db.commit()

    back = await stock_consume.restore_layers(db, org, pid, loc, 2, order_id=oid)
    assert back == 2
    await db.commit()
    assert await _layers(db, pid) == [(10, 2), (30, 1)]          # was [(10, 1), (30, 2)]
    db.expire_all()
    from backend.models import OrderLine
    ln = (await db.execute(select(OrderLine).where(OrderLine.id == line_id))).scalar_one()
    assert not (ln.stock_draws or {}).get("layers")              # nothing left drawn on the line


async def test_partial_restore_gives_back_the_last_drawn_first_and_the_named_line_first(client, tenant, db):
    from backend.services import stock_consume
    loc = await K.loc(client, tenant)
    pid = await K.part(client, tenant, loc, "Screen", 99, 10, qty=0)
    await _receive(client, tenant, pid, loc, 1, 10)
    await _receive(client, tenant, pid, loc, 1, 20)
    await _receive(client, tenant, pid, loc, 1, 30)
    org, oid, a = await _job_line(client, tenant, db, loc, pid, 2)
    await stock_consume.consume_for_line(db, org_id=org, order_id=oid, line=a, location_id=loc, qty=2)
    from backend.models import OrderLine
    b = OrderLine(org_id=org, order_id=oid, line_type="part", kind="product", ref_id=pid,
                  description="Screen", qty=1, unit_price=99, cost_price=0)
    db.add(b)
    await db.flush()
    await stock_consume.consume_for_line(db, org_id=org, order_id=oid, line=b, location_id=loc, qty=1)
    await db.commit()
    assert await _layers(db, pid) == [(10, 0), (20, 0), (30, 0)]
    # line a's qty goes down by one: its last-drawn unit (20) comes back, not line b's (30)
    await stock_consume.restore_layers(db, org, pid, loc, 1, order_id=oid, line=a)
    await db.commit()
    assert await _layers(db, pid) == [(10, 0), (20, 1), (30, 0)]


async def test_a_line_without_a_journal_still_restores_newest_first(client, tenant, db):
    """Lines drawn before the journal existed have none: the old per-location rule still applies."""
    from backend.services import stock_consume
    loc = await K.loc(client, tenant)
    pid = await K.part(client, tenant, loc, "Screen", 99, 10, qty=0)
    await _receive(client, tenant, pid, loc, 1, 10)
    await _receive(client, tenant, pid, loc, 1, 30)
    org, oid, line = await _job_line(client, tenant, db, loc, pid, 1)
    await stock_consume.consume_for_line(db, org_id=org, order_id=oid, line=line, location_id=loc, qty=1)
    line.stock_draws = None
    await db.commit()
    assert await stock_consume.restore_layers(db, org, pid, loc, 1, order_id=oid) == 1
    await db.commit()
    assert await _layers(db, pid) == [(10, 1), (30, 1)]


# ── INV-06: walk-in returns linked to the sale ──────────────────────────────────────────────────
async def _sold(client, tenant, db, qty_sold=2):
    from backend.tests.test_b5_pos_quote import _till
    till = await _till(client, tenant)
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    pid = await K.part(client, tenant, loc, "Charger", 40, 10, qty=0)
    await _receive(client, tenant, pid, loc, 3, 10)
    await _receive(client, tenant, pid, loc, 3, 30)
    r = await client.post("/api/sales", json={"account_id": till, "lines": [
        {"kind": "product", "ref_id": pid, "description": "Charger", "qty": qty_sold, "unit_price": 40}]},
        headers=tenant)
    assert r.status_code in (200, 201), r.text
    return r.json()["id"], pid, loc, till


async def test_desk_returns_goods_against_the_sale_onto_its_own_layers(client, tenant, db):
    from backend.models import ClientReturn
    sale_id, pid, loc, _till = await _sold(client, tenant, db)
    assert await _layers(db, pid) == [(10, 1), (30, 3)]
    desk = await _staff(client, tenant, {"can_create_returns": True, "can_manage_inventory": False})
    r = await client.post("/api/returns", headers=desk, json={
        "sale_id": sale_id, "lines": [{"part_id": pid, "qty": 1}]})
    assert r.status_code == 201, r.text                       # was 403: no inventory rights
    assert await _on_hand(db, pid) == 5
    assert await _layers(db, pid) == [(10, 2), (30, 3)]       # the sale's own layer, no new one
    ret = (await db.execute(select(ClientReturn).where(ClientReturn.id == r.json()["id"]))).scalar_one()
    assert ret.sale_id == sale_id
    sale = (await client.get(f"/api/sales/{sale_id}", headers=tenant)).json()
    assert float(sale["lines"][0]["returned"]) == 1
    await _ledger_matches(db, pid)


async def test_walk_in_return_can_not_bring_back_more_than_the_sale_sold(client, tenant, db):
    sale_id, pid, _loc, till = await _sold(client, tenant, db, qty_sold=2)
    r = await client.post("/api/returns", headers=tenant, json={
        "sale_id": sale_id, "lines": [{"part_id": pid, "qty": 3}]})
    assert r.status_code == 409, r.text
    assert "2" in r.json()["detail"]
    r = await client.post("/api/returns", headers=tenant, json={
        "sale_id": sale_id, "lines": [{"part_id": pid, "qty": 1}]})
    assert r.status_code == 201, r.text
    # the till return and the walk-in return share one cap: 1 left
    line_id = (await client.get(f"/api/sales/{sale_id}", headers=tenant)).json()["lines"][0]["id"]
    r = await client.post(f"/api/sales/{sale_id}/returns", headers=tenant, json={
        "account_id": till, "lines": [{"line_id": line_id, "qty": 2}]})
    assert r.status_code == 400, r.text
    r = await client.post(f"/api/sales/{sale_id}/returns", headers=tenant, json={
        "account_id": till, "lines": [{"line_id": line_id, "qty": 1}]})
    assert r.status_code == 201, r.text
    r = await client.post("/api/returns", headers=tenant, json={
        "sale_id": sale_id, "lines": [{"part_id": pid, "qty": 1}]})
    assert r.status_code == 409, r.text                      # everything sold is back
    assert await _on_hand(db, pid) == 6
    await _ledger_matches(db, pid)


async def test_walk_in_return_against_a_sale_refuses_a_product_not_on_it(client, tenant, db):
    sale_id, _pid, loc, _till = await _sold(client, tenant, db)
    other = await K.part(client, tenant, loc, "Cable", 5, 1, qty=0)
    r = await client.post("/api/returns", headers=tenant, json={
        "sale_id": sale_id, "lines": [{"part_id": other, "qty": 1}]})
    assert r.status_code == 409, r.text
    assert "sale" in r.json()["detail"].lower()
    r = await client.post("/api/returns", headers=tenant, json={
        "sale_id": "nope", "lines": [{"part_id": other, "qty": 1}]})
    assert r.status_code == 404, r.text
    r = await client.post("/api/returns", headers=tenant, json={
        "sale_id": sale_id, "order_id": "x", "lines": [{"part_id": other, "qty": 1}]})
    assert r.status_code == 422, r.text


async def test_serialized_walk_in_return_against_a_sale_takes_only_its_units(client, tenant, db):
    from backend.tests.test_b5_pos_quote import _till
    from backend.tests.test_qa_f2_stock import _phone
    p, _loc, sns = await _phone(client, tenant, n=2)
    till = await _till(client, tenant)
    s1 = (await client.post("/api/sales", json={"account_id": till, "lines": [{
        "kind": "product", "ref_id": p["id"], "description": "iPhone 13", "qty": 1, "unit_price": 400,
        "serials": [sns[0]]}]}, headers=tenant)).json()
    s2 = (await client.post("/api/sales", json={"account_id": till, "lines": [{
        "kind": "product", "ref_id": p["id"], "description": "iPhone 13", "qty": 1, "unit_price": 400,
        "serials": [sns[1]]}]}, headers=tenant)).json()
    r = await client.post("/api/returns", headers=tenant, json={
        "sale_id": s1["id"], "lines": [{"part_id": p["id"], "qty": 1, "serials": [sns[1]]}]})
    assert r.status_code == 409, r.text                      # that phone left on the other sale
    r = await client.post("/api/returns", headers=tenant, json={
        "sale_id": s1["id"], "lines": [{"part_id": p["id"], "qty": 1, "serials": [sns[0]]}]})
    assert r.status_code == 201, r.text
    assert await _on_hand(db, p["id"]) == 1
    assert s2["id"]


async def test_walk_in_return_without_a_sale_is_still_gated(client, tenant, db):
    _sale_id, pid, _loc, _till = await _sold(client, tenant, db)
    desk = await _staff(client, tenant, {"can_create_returns": True, "can_manage_inventory": False})
    r = await client.post("/api/returns", headers=desk, json={"lines": [{"part_id": pid, "qty": 1}]})
    assert r.status_code == 403, r.text


# ── stock_rows.totals within reach ──────────────────────────────────────────────────────────────
async def test_availability_counts_only_the_warehouses_the_person_can_reach(client, tenant, db):
    """W1 (reachable) holds 2, W2 at the same location (not reachable) holds 3."""
    from backend.tests.test_e1_inventory_reach import _two_warehouses
    loc, _w1, _w2, pid, H = await _two_warehouses(client, tenant, db)
    # a hold: 'Only 2 available', not a hold on 4 units that only 2 reachable ones back
    r = await client.post("/api/inventory/reserve", headers=H, json={"part_id": pid, "location_id": loc, "qty": 4})
    assert r.status_code == 409, r.text                      # was 201
    assert "2" in r.json()["detail"]
    # the till: the honest 'Only 2 here', not 'stock changed, retry'
    from backend.tests.test_b5_pos_quote import _till
    till = await _till(client, tenant)
    r = await client.post("/api/sales", headers=H, json={"account_id": till, "lines": [
        {"kind": "product", "ref_id": pid, "description": "Screen", "qty": 3, "unit_price": 99}]})
    assert r.status_code == 409, r.text
    assert "Only 2" in r.json()["detail"], r.text
    # the owner still sees all 5
    from backend.services import stock_rows
    db.expire_all()
    assert (await stock_rows.totals(db, pid, loc))[0] == 5


async def test_totals_inside_a_request_follow_the_actor(client, tenant, db):
    from backend.services import stock_rows
    from backend.tests.test_e1_inventory_reach import _two_warehouses
    loc, _w1, _w2, pid, H = await _two_warehouses(client, tenant, db)
    me = (await client.get("/api/auth/me", headers=H)).json()
    db.info["actor_id"] = me["id"]
    try:
        assert await stock_rows.totals(db, pid, loc) == (2, 0)
        assert await stock_rows.free(db, pid, loc) == 2
    finally:
        db.info.pop("actor_id", None)
        db.info.pop("warehouse_reach", None)


# ── stocktake vs reservations ───────────────────────────────────────────────────────────────────
async def test_stocktake_below_reserved_trims_the_holds_and_says_so(client, tenant, db):
    from backend.models import StockLevel, StockReservation
    from backend.tests.test_stocktake_never_negative import _count
    from backend.tests.test_stocktake_postings_print import _loc, _part, _stock, _wh
    w = await _wh(client, tenant)
    loc = await _loc(client, tenant)
    p = await _part(client, tenant, sku="RSV-1", barcode="RSV-1")
    await _stock(client, tenant, p["id"], 5)
    old, _ = await K.job(client, tenant, loc)
    new, _ = await K.job(client, tenant, loc)
    for oid, q in ((old, 2), (new, 2)):
        r = await client.post("/api/inventory/reserve", headers=tenant,
                              json={"part_id": p["id"], "location_id": loc, "qty": q, "order_id": oid})
        assert r.status_code == 201, r.text
    st = (await client.post("/api/stocktake", json={"warehouse_id": w["id"], "kind": "full"},
                            headers=tenant)).json()
    await _count(client, tenant, st["id"], p["id"], 3)       # 3 on the shelf, 4 reserved
    r = await client.post(f"/api/stocktake/{st['id']}/post", headers=tenant)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["adjustments"] == 1
    assert body.get("warnings"), body                        # says which holds were trimmed
    assert "1" in body["warnings"][0]
    db.expire_all()
    avail, held = (await db.execute(select(func.sum(StockLevel.qty_available), func.sum(StockLevel.qty_reserved))
                                    .where(StockLevel.part_id == p["id"]))).one()
    assert float(avail) == 3 and float(held) == 3            # was reserved 4 against 3 on hand
    holds = {h.order_id: float(h.qty) for h in (await db.execute(select(StockReservation).where(
        StockReservation.part_id == p["id"], StockReservation.released == False))).scalars()}  # noqa: E712
    assert holds == {old: 2, new: 1}                         # the newest hold gives way first
    await _ledger_matches(db, p["id"])


async def test_stocktake_above_reserved_leaves_holds_alone(client, tenant, db):
    from backend.models import StockLevel
    from backend.tests.test_stocktake_never_negative import _count
    from backend.tests.test_stocktake_postings_print import _loc, _part, _stock, _wh
    w = await _wh(client, tenant)
    loc = await _loc(client, tenant)
    p = await _part(client, tenant, sku="RSV-2", barcode="RSV-2")
    await _stock(client, tenant, p["id"], 5)
    r = await client.post("/api/inventory/reserve", headers=tenant,
                          json={"part_id": p["id"], "location_id": loc, "qty": 2})
    assert r.status_code == 201, r.text
    st = (await client.post("/api/stocktake", json={"warehouse_id": w["id"], "kind": "full"},
                            headers=tenant)).json()
    await _count(client, tenant, st["id"], p["id"], 4)
    r = await client.post(f"/api/stocktake/{st['id']}/post", headers=tenant)
    assert r.status_code == 200, r.text
    assert not r.json().get("warnings")
    db.expire_all()
    held = (await db.execute(select(func.sum(StockLevel.qty_reserved)).where(
        StockLevel.part_id == p["id"]))).scalar_one()
    assert float(held) == 2


# The job routes (routers/orders.py, group A) take stock through their own `_consume_from_bins` and
# give it back through `_reverse_fulfillment`; they need two keyword arguments to use the journal
# (`journal=line` on the layer draw, `order_id=order.id` on the restore). That edit is outside this
# group's files and is handed to the lead as a patch; this test turns green with it (drop the marker).
@pytest.mark.xfail(strict=True, reason="needs the orders.py journal hook (wave-4 follow-up for the lead)")
async def test_removing_a_job_line_refills_the_layers_it_drew(client, tenant, db):
    loc = await K.loc(client, tenant)
    pid = await K.part(client, tenant, loc, "Screen", 99, 10, qty=0)
    await _receive(client, tenant, pid, loc, 2, 10)
    await _receive(client, tenant, pid, loc, 2, 30)
    oid, _ = await K.job(client, tenant, loc)
    await K.add_line(client, tenant, oid, ref_id=pid, qty=2, **P)
    assert await _layers(db, pid) == [(10, 0), (30, 2)]
    r = await client.post(f"/api/inventory/parts/{pid}/adjust", headers=tenant, json={
        "location_id": loc, "qty": 1, "movement_type": "writeoff"})
    assert r.status_code == 200, r.text
    assert await _layers(db, pid) == [(10, 0), (30, 1)]
    line = [ln for ln in await K.lines(client, tenant, oid) if ln["kind"] == "product"][0]
    r = await client.delete(f"/api/orders/{oid}/lines/{line['id']}", headers=tenant)
    assert r.status_code in (200, 204), r.text
    assert await _layers(db, pid) == [(10, 2), (30, 1)]          # was [(10, 1), (30, 2)]
    await _ledger_matches(db, pid)
