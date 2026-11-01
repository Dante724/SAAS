"""List endpoints cost a fixed number of queries, however many rows they return (audit 29 Oct 2026).

Each test seeds a few rows, counts the queries one GET runs, seeds more rows (each with its own
related records, so a per-row lookup would show up), and asserts the count did not grow.
Rows are inserted straight into the database to keep the tests quick.
"""
import uuid
from datetime import datetime

import pytest
from sqlalchemy import event

from backend.models import (
    Account,
    Bundle,
    BundleComponent,
    Call,
    Client,
    ClientReturn,
    ClientReturnLine,
    CustomField,
    Employee,
    EmployeeBalanceLedger,
    Feedback,
    Order,
    OrderChecklist,
    OrderChecklistItem,
    Part,
    POLine,
    PurchaseOrder,
    PurchaseReturn,
    PurchaseReturnLine,
    Refund,
    Service,
    StockLevel,
    StockReservation,
    StockTransfer,
    StockTransferLine,
    StorageBin,
    Supplier,
    WorkOrderType,
    WriteOff,
    WriteOffLine,
)


class _Counter:
    def __init__(self, engine):
        self.engine, self.n = engine, 0

    def _hit(self, *a, **k):
        self.n += 1

    def __enter__(self):
        event.listen(self.engine, "before_cursor_execute", self._hit)
        return self

    def __exit__(self, *exc):
        event.remove(self.engine, "before_cursor_execute", self._hit)


def _u():
    return uuid.uuid4().hex[:8]


async def _ctx(client, h):
    me = (await client.get("/api/auth/me", headers=h)).json()
    loc = (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]
    return me["org_id"], loc, me["id"]


async def _queries(client, db, url, h):
    with _Counter(db.bind.sync_engine) as c:
        r = await client.get(url, headers=h)
    assert r.status_code == 200, (url, r.text)
    return c.n, r.json()


async def _assert_flat(client, db, h, url, seed, *, few=2, more=6, rows=None):
    """seed(n) adds n rows; the query count with few+more rows must equal the count with few."""
    await seed(few)
    await client.get(url, headers=h)            # warm up one-off lazy seeding (statuses, numbering)
    n_few, body_few = await _queries(client, db, url, h)
    assert n_few > 0, "the counter isn't attached to the engine the app uses"
    await seed(more)
    n_many, body_many = await _queries(client, db, url, h)
    if rows:
        assert rows(body_many) - rows(body_few) == more, (rows(body_few), rows(body_many))
    assert n_many == n_few, f"{url}: {n_few} queries with {few} rows, {n_many} with {few + more}"


def _items(body):
    return len(body["items"])


def _order(org, loc, **kw):
    return Order(org_id=org, location_id=loc, order_number=f"T-{_u()}", **kw)


@pytest.mark.asyncio
async def test_client_returns_list_is_flat(client, tenant, db):
    org, loc, _ = await _ctx(client, tenant)

    async def seed(n):
        for _ in range(n):
            c = Client(org_id=org, name=f"C {_u()}")
            p = Part(org_id=org, name=f"P {_u()}")
            db.add_all([c, p])
            await db.flush()
            o = _order(org, loc, client_id=c.id)
            db.add(o)
            await db.flush()
            r = ClientReturn(org_id=org, location_id=loc, order_id=o.id, client_id=c.id, reason="faulty")
            db.add(r)
            await db.flush()
            db.add(ClientReturnLine(return_id=r.id, part_id=p.id, qty=1))
        await db.commit()

    await _assert_flat(client, db, tenant, "/api/returns", seed, rows=_items)


@pytest.mark.asyncio
async def test_refunds_list_is_flat(client, tenant, db):
    org, loc, _ = await _ctx(client, tenant)

    async def seed(n):
        for _ in range(n):
            a = Account(org_id=org, name=f"Till {_u()}")
            o = _order(org, loc)
            db.add_all([a, o])
            await db.flush()
            db.add(Refund(org_id=org, account_id=a.id, order_id=o.id, amount=5))
        await db.commit()

    await _assert_flat(client, db, tenant, "/api/returns/refunds", seed, rows=_items)


@pytest.mark.asyncio
async def test_supplier_returns_and_write_offs_lists_are_flat(client, tenant, db):
    org, _, _ = await _ctx(client, tenant)

    async def seed_returns(n):
        for _ in range(n):
            p = Part(org_id=org, name=f"P {_u()}")
            r = PurchaseReturn(org_id=org)
            db.add_all([p, r])
            await db.flush()
            db.add(PurchaseReturnLine(return_id=r.id, part_id=p.id, qty=1))
        await db.commit()

    async def seed_write_offs(n):
        for _ in range(n):
            p = Part(org_id=org, name=f"P {_u()}")
            w = WriteOff(org_id=org)
            db.add_all([p, w])
            await db.flush()
            db.add(WriteOffLine(write_off_id=w.id, part_id=p.id, qty=1))
        await db.commit()

    await _assert_flat(client, db, tenant, "/api/stock-docs/returns", seed_returns, rows=_items)
    await _assert_flat(client, db, tenant, "/api/stock-docs/write-offs", seed_write_offs, rows=_items)


@pytest.mark.asyncio
async def test_bins_list_sums_stock_in_one_query(client, tenant, db):
    org, loc, _ = await _ctx(client, tenant)

    async def seed(n):
        for _ in range(n):
            b = StorageBin(org_id=org, location_id=loc, name=f"B {_u()}")
            p = Part(org_id=org, name=f"P {_u()}")
            db.add_all([b, p])
            await db.flush()
            db.add(StockLevel(part_id=p.id, location_id=loc, bin_id=b.id, qty_available=3))
        await db.commit()

    await _assert_flat(client, db, tenant, "/api/bins", seed, rows=_items)
    items = (await client.get("/api/bins", headers=tenant)).json()["items"]
    assert {i["qty_on_hand"] for i in items} == {3}


@pytest.mark.asyncio
async def test_catalog_search_prices_bundles_without_a_query_per_component(client, tenant, db):
    org, _, _ = await _ctx(client, tenant)
    b = Bundle(org_id=org, name="Kit A", price_mode="sum")
    db.add(b)
    await db.flush()
    bid = b.id
    await db.commit()

    async def seed(n):
        for _ in range(n):
            p = Part(org_id=org, name=f"P {_u()}", sell_price=2)
            s = Service(org_id=org, name=f"S {_u()}", price=3)
            db.add_all([p, s])
            await db.flush()
            db.add_all([BundleComponent(bundle_id=bid, ref_type="product", ref_id=p.id, qty=1),
                        BundleComponent(bundle_id=bid, ref_type="service", ref_id=s.id, qty=1)])
        await db.commit()

    await _assert_flat(client, db, tenant, "/api/catalog/search?type=bundle", seed)
    kit = next(i for i in (await client.get("/api/catalog/search?type=bundle", headers=tenant)).json()["items"]
               if i["ref_id"] == bid)
    assert kit["price"] == pytest.approx(8 * 5) and kit["components"] == 16


@pytest.mark.asyncio
async def test_incoming_stock_is_flat(client, tenant, db):
    org, loc, _ = await _ctx(client, tenant)
    sup = Supplier(org_id=org, name="Acme")
    db.add(sup)
    await db.flush()
    sid = sup.id
    await db.commit()

    async def seed(n):
        for _ in range(n):
            p = Part(org_id=org, name=f"P {_u()}")
            o = _order(org, loc)
            po = PurchaseOrder(supplier_id=sid, location_id=loc, po_number=f"PO-{_u()}", status="ordered")
            db.add_all([p, o, po])
            await db.flush()
            db.add(POLine(po_id=po.id, part_id=p.id, qty_ordered=4, qty_received=0, reserved_order_id=o.id))
        await db.commit()

    await _assert_flat(client, db, tenant, "/api/suppliers/purchase-orders/incoming", seed, rows=_items)
    assert all(i["reserved_for"] for i in
               (await client.get("/api/suppliers/purchase-orders/incoming", headers=tenant)).json()["items"])


@pytest.mark.asyncio
async def test_order_purchase_orders_panel_is_flat(client, tenant, db):
    org, loc, _ = await _ctx(client, tenant)
    o = _order(org, loc)
    sup = Supplier(org_id=org, name="Acme")
    db.add_all([o, sup])
    await db.flush()
    oid, sid = o.id, sup.id
    await db.commit()

    async def seed(n):
        for _ in range(n):
            p = Part(org_id=org, name=f"P {_u()}")
            po = PurchaseOrder(supplier_id=sid, location_id=loc, po_number=f"PO-{_u()}",
                               status="ordered", source_order_id=oid)
            db.add_all([p, po])
            await db.flush()
            db.add(POLine(po_id=po.id, part_id=p.id, qty_ordered=1, reserved_order_id=oid))
        await db.commit()

    await _assert_flat(client, db, tenant, f"/api/orders/{oid}/purchase-orders", seed)
    await _assert_flat(client, db, tenant, f"/api/orders/{oid}/incoming-po", seed)


@pytest.mark.asyncio
async def test_payroll_calculation_sums_every_employee_in_one_query(client, tenant, db):
    org, _, _ = await _ctx(client, tenant)
    month = datetime.utcnow().strftime("%Y-%m")

    async def seed(n):
        for _ in range(n):
            e = Employee(org_id=org, name=f"E {_u()}", email=f"{_u()}@t.dev", password_hash="x",
                         wage_type="fixed", wage_value=100)
            db.add(e)
            await db.flush()
            for kind, amount, ref in (("commission", 12, "order"), ("clawback", -2, "refund"),
                                      ("hourly_wage", 5, "timesheet"), ("bonus", 3, "manual"),
                                      ("penalty", -2, "manual"),
                                      ("bonus", 50, "rule")):      # not manual: not on this page
                db.add(EmployeeBalanceLedger(org_id=org, employee_id=e.id, entry_type=kind,
                                             amount=amount, ref_type=ref))
        await db.commit()

    url = f"/api/payroll/calculation?month={month}"
    await _assert_flat(client, db, tenant, url, seed)
    rows = [i for i in (await client.get(url, headers=tenant)).json()["items"] if i["name"].startswith("E ")]
    assert len(rows) == 8
    for r in rows:
        assert (r["commissions_total"], r["hourly_total"], r["bonuses_total"], r["penalties_total"]) == (10, 5, 3, 2)
        assert r["computed_total"] == pytest.approx(100 + 10 + 5 + 3 - 2)


@pytest.mark.asyncio
async def test_transfers_list_is_flat(client, tenant, db):
    org, loc, _ = await _ctx(client, tenant)

    async def seed(n):
        for _ in range(n):
            p = Part(org_id=org, name=f"P {_u()}")
            t = StockTransfer(org_id=org, transfer_number=f"TR-{_u()}", from_location_id=loc, to_location_id=loc)
            db.add_all([p, t])
            await db.flush()
            db.add(StockTransferLine(transfer_id=t.id, part_id=p.id, qty=1))
        await db.commit()

    await _assert_flat(client, db, tenant, "/api/transfers", seed, rows=_items)
    assert all(i["lines"][0]["part_name"] for i in (await client.get("/api/transfers", headers=tenant)).json()["items"])


@pytest.mark.asyncio
async def test_reservations_feedback_calls_types_and_duplicates_are_flat(client, tenant, db):
    org, loc, _ = await _ctx(client, tenant)

    async def seed_reservations(n):
        for _ in range(n):
            p = Part(org_id=org, name=f"P {_u()}")
            db.add(p)
            await db.flush()
            db.add(StockReservation(org_id=org, part_id=p.id, location_id=loc, qty=1))
        await db.commit()

    async def seed_feedback(n):
        for _ in range(n):
            o = _order(org, loc)
            db.add(o)
            await db.flush()
            db.add(Feedback(org_id=org, order_id=o.id, system="nps"))
        await db.commit()

    async def seed_calls(n):
        for _ in range(n):
            c = Client(org_id=org, name=f"C {_u()}")
            db.add(c)
            await db.flush()
            db.add(Call(org_id=org, direction="inbound", status="ringing", client_id=c.id,
                        from_number="+1555", created_at=datetime.utcnow()))
        await db.commit()

    async def seed_types(n):
        for _ in range(n):
            t = WorkOrderType(org_id=org, name=f"T {_u()}")
            db.add(t)
            await db.flush()
            db.add(CustomField(org_id=org, entity_type="order", type_id=t.id, field_key=f"k{_u()}",
                               field_label="K", field_type="text"))
        await db.commit()

    async def seed_dupes(n):
        for _ in range(n):
            phone = f"+1{_u()}"
            db.add_all([Client(org_id=org, name="A", phone=phone), Client(org_id=org, name="B", phone=phone)])
        await db.commit()

    await _assert_flat(client, db, tenant, "/api/inventory/reservations", seed_reservations, rows=_items)
    await _assert_flat(client, db, tenant, "/api/feedback/list", seed_feedback, rows=_items)
    await _assert_flat(client, db, tenant, "/api/telephony/incoming", seed_calls, rows=_items)
    await _assert_flat(client, db, tenant, "/api/forms/types", seed_types, rows=_items)
    await _assert_flat(client, db, tenant, "/api/clients/duplicates", seed_dupes, rows=_items)


@pytest.mark.asyncio
async def test_order_checklists_list_is_flat(client, tenant, db):
    org, loc, _ = await _ctx(client, tenant)
    o = _order(org, loc)
    db.add(o)
    await db.flush()
    oid = o.id
    await db.commit()

    async def seed(n):
        for _ in range(n):
            author = Employee(org_id=org, name=f"A {_u()}", email=f"{_u()}@t.dev", password_hash="x")
            db.add(author)
            await db.flush()
            cl = OrderChecklist(org_id=org, order_id=oid, title=f"CL {_u()}", author_id=author.id)
            db.add(cl)
            await db.flush()
            db.add_all([OrderChecklistItem(org_id=org, order_id=oid, checklist_id=cl.id, label=f"i{k}")
                        for k in range(3)])
        await db.commit()

    await _assert_flat(client, db, tenant, f"/api/orders/{oid}/checklists", seed, rows=_items)
    items = (await client.get(f"/api/orders/{oid}/checklists", headers=tenant)).json()["items"]
    assert all(i["author"].startswith("A ") for i in items)


@pytest.mark.asyncio
async def test_low_stock_report_sums_stock_in_sql(client, tenant, db):
    org, loc, _ = await _ctx(client, tenant)
    low = Part(org_id=org, name="Low", min_stock=3)
    ok = Part(org_id=org, name="Ok", min_stock=2)
    empty = Part(org_id=org, name="Empty", min_stock=1)
    db.add_all([low, ok, empty])
    await db.flush()
    db.add_all([StockLevel(part_id=low.id, location_id=loc, qty_available=1),
                StockLevel(part_id=ok.id, location_id=loc, qty_available=5)])
    await db.commit()
    body = (await client.get("/api/reports/inventory/low-stock", headers=tenant)).json()
    got = {i["name"]: (i["qty"], i["min_stock"]) for i in body["items"]}
    assert got["Low"] == (1, 3) and got["Empty"] == (0, 1) and "Ok" not in got
    assert body["count"] == len(body["items"])
