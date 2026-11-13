"""Wave 4 / F1: reports and dashboards (SEC-04, selected-with-no-locations, PERF-12 rest, RPT-06).

SEC-04: a role limited to some locations (or some warehouses) saw the whole shop's numbers on the
Reports dashboard, the status counts, the Home feed, the finance summary and every report that
didn't read ?location_id (technicians, sold products/services, labour, every inventory report).

Selected-with-none: a role with location_scope="selected" and no locations ticked is "no access"
everywhere in reports, and the role editor refuses to save one.

PERF-12: the technicians, labour and cash-flow reports add up in SQL (GROUP BY) instead of loading
every row into Python; the results are compared here with the old Python implementation. The xlsx
and CSV exports are written row by row; the appointments and products lists take an opt-in page.

RPT-06: the accountant export's date-only "To" runs to the last microsecond of that shop day.
"""
import io
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from backend.models import Order

pytestmark = pytest.mark.asyncio

REPORT_PERMS = {"can_view_reports": True, "can_view_finance": True, "can_see_profit": True,
                "can_view_purchase_price": True, "can_view_cashflow": True, "can_view_balance": True,
                "can_view_payroll": True, "can_export_sales": True, "can_view_orders": True,
                "can_view_inventory": True}


async def _scoped_login(client, db, org_id, *, email, permissions=None, **role_kw):
    """A role with the given scope and permissions, an employee holding it, logged in."""
    from backend.models import Employee, Role
    from backend.services.auth import hash_password
    role = Role(org_id=org_id, name=f"Scoped {uuid.uuid4().hex[:4]}", permissions=permissions or {}, **role_kw)
    db.add(role)
    await db.flush()
    emp = Employee(org_id=org_id, name="Scoped user", email=email,
                   password_hash=hash_password("pw-123456789"), active=True, role_id=role.id)
    db.add(emp)
    await db.commit()
    await db.refresh(emp)
    tok = (await client.post("/api/auth/token", data={
        "username": email, "password": "pw-123456789"})).json()["access_token"]
    return {"Authorization": f"Bearer {tok}"}, emp, role


async def _me(client, H):
    return (await client.get("/api/auth/me", headers=H)).json()


async def _loc(client, H):
    return (await client.get("/api/staff/locations", headers=H)).json()["items"][0]["id"]


async def _closed_key(client, H, group="closed"):
    sts = (await client.get("/api/statuses", headers=H)).json()
    return next(s["key"] for s in (sts.get("items", sts) if isinstance(sts, dict) else sts)
                if s.get("status_group") == group)


async def _job(client, H, loc, price, *, close=True):
    cid = (await client.post("/api/clients", json={"name": "C"}, headers=H)).json()["id"]
    oid = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc}, headers=H)).json()["id"]
    r = await client.post(f"/api/orders/{oid}/lines", headers=H, json={
        "line_type": "labour", "kind": "service", "description": f"Fix {price}", "qty": 1, "unit_price": price})
    assert r.status_code in (200, 201), r.text
    if close:
        r = await client.patch(f"/api/orders/{oid}/status", json={"status": await _closed_key(client, H)}, headers=H)
        assert r.status_code == 200, r.text
    return oid


async def _branch(db, org_id, name="Branch B"):
    from backend.models import Location
    b = Location(org_id=org_id, name=name)
    db.add(b)
    await db.commit()
    await db.refresh(b)
    return b.id


async def _two_branches(client, tenant, db):
    """A 1000 job at the main branch, a 100 job at branch B, and a login limited to B."""
    from backend.routers import reports
    reports._dash_cache.clear()
    me = await _me(client, tenant)
    main = await _loc(client, tenant)
    b = await _branch(db, me["org_id"])
    o_main = await _job(client, tenant, main, 1000)
    o_b = await _job(client, tenant, b, 100)
    for o in (await db.execute(select(Order).where(Order.id.in_([o_main, o_b])))).scalars().all():
        o.assigned_to_id = me["id"]                     # the technicians report credits the assignee
    await db.commit()
    H, emp, role = await _scoped_login(
        client, db, me["org_id"], email=f"loc-{uuid.uuid4().hex[:6]}@x.dev",
        permissions=REPORT_PERMS, perms_explicit=True,
        location_scope="selected", location_ids=f'["{b}"]')
    return {"org": me["org_id"], "main": main, "b": b, "o_main": o_main, "o_b": o_b, "H": H, "emp": emp, "role": role}


# ── SEC-04: location scope ───────────────────────────────────────────────────────────────────────

async def test_reports_dashboard_covers_only_the_callers_locations(client, tenant, db):
    s = await _two_branches(client, tenant, db)
    own = (await client.get("/api/reports/dashboard", headers=tenant)).json()
    assert own["stats"]["total_orders"] == 2
    assert own["stats"]["revenue"] == 1100

    r = await client.get("/api/reports/dashboard", headers=s["H"])
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["stats"]["total_orders"] == 1                        # was 2: the whole shop
    assert d["stats"]["revenue"] == 100                           # was 1100
    assert d["stats"]["total_clients"] == 2                       # clients are shop-wide
    assert [o["id"] for o in d["recent_orders"]] == [s["o_b"]]
    assert sum(d["orders_by_status"].values()) == 1
    # and the owner's cached tiles are not served to the limited caller (or back)
    again = (await client.get("/api/reports/dashboard", headers=tenant)).json()
    assert again["stats"]["total_orders"] == 2


async def test_orders_by_status_counts_only_the_callers_locations(client, tenant, db):
    s = await _two_branches(client, tenant, db)
    assert sum((await client.get("/api/reports/orders/by-status", headers=tenant)).json().values()) == 2
    assert sum((await client.get("/api/reports/orders/by-status", headers=s["H"])).json().values()) == 1


async def test_home_feed_counts_only_the_callers_locations(client, tenant, db):
    s = await _two_branches(client, tenant, db)
    main_open = await _job(client, tenant, s["main"], 50, close=False)
    o = (await db.execute(select(Order).where(Order.id == main_open))).scalar_one()
    o.priority = 2
    await db.commit()
    own = (await client.get("/api/reports/home-feed", headers=tenant)).json()
    assert any(i["key"] == "urgent" for i in own["items"])
    mine = (await client.get("/api/reports/home-feed", headers=s["H"])).json()
    assert not any(i["key"] == "urgent" for i in mine["items"])     # the main branch's urgent job


async def test_finance_summary_covers_only_the_callers_locations(client, tenant, db):
    s = await _two_branches(client, tenant, db)
    own = (await client.get("/api/finance/reports/summary", headers=tenant)).json()
    assert own["total_revenue"] == 1100
    r = await client.get("/api/finance/reports/summary", headers=s["H"])
    assert r.status_code == 200, r.text
    assert r.json()["total_revenue"] == 100                        # was 1100


@pytest.mark.parametrize("key,col", [("technicians", 2), ("provided_services", 2)])
async def test_job_reports_follow_the_callers_branch(client, tenant, db, key, col):
    s = await _two_branches(client, tenant, db)
    own = (await client.get(f"/api/reports/run/{key}", headers=tenant)).json()
    assert round(sum(r[col] for r in own["rows"]), 2) == 1100
    mine = await client.get(f"/api/reports/run/{key}", headers=s["H"])
    assert mine.status_code == 200, mine.text
    assert round(sum(r[col] for r in mine.json()["rows"]), 2) == 100      # was 1100


# ── selected with no locations = no access ───────────────────────────────────────────────────────

async def _no_locations(client, tenant, db):
    from backend.routers import reports
    reports._dash_cache.clear()
    me = await _me(client, tenant)
    main = await _loc(client, tenant)
    await _job(client, tenant, main, 1000)
    H, _emp, _role = await _scoped_login(
        client, db, me["org_id"], email=f"none-{uuid.uuid4().hex[:6]}@x.dev",
        permissions=REPORT_PERMS, perms_explicit=True, location_scope="selected", location_ids="[]")
    return H, main


@pytest.mark.parametrize("url", [
    "/api/reports/dashboard", "/api/reports/run/technicians", "/api/reports/run/stock_levels",
    "/api/reports/export-csv/technicians", "/api/reports/company-dashboard", "/api/reports/total-profit",
    "/api/reports/job-profitability", "/api/finance/reports/summary", "/api/reports/orders/by-status",
])
async def test_selected_with_no_locations_sees_no_figures(client, tenant, db, url):
    H, _main = await _no_locations(client, tenant, db)
    r = await client.get(url, headers=H)
    assert r.status_code == 403, (url, r.status_code, r.text)


async def test_selected_with_no_locations_naming_a_location_is_refused(client, tenant, db):
    H, main = await _no_locations(client, tenant, db)
    r = await client.get(f"/api/reports/run/technicians?location_id={main}", headers=H)
    assert r.status_code == 403, r.text


async def test_home_feed_with_no_locations_shows_no_job_counts(client, tenant, db):
    H, main = await _no_locations(client, tenant, db)
    await _job(client, tenant, main, 10, close=False)
    r = await client.get("/api/reports/home-feed", headers=H)
    assert r.status_code == 200, r.text
    assert not {i["key"] for i in r.json()["items"]} & {"overdue", "messages", "ready", "urgent", "unpaid"}


async def test_role_editor_refuses_selected_with_no_locations(client, tenant, db):
    me = await _me(client, tenant)
    r = await client.post("/api/settings/rbac/roles", headers=tenant, json={
        "name": "Nowhere", "location_scope": "selected", "location_ids": []})
    assert r.status_code == 422, r.text
    main = await _loc(client, tenant)
    r = await client.post("/api/settings/rbac/roles", headers=tenant, json={
        "name": "Main only", "location_scope": "selected", "location_ids": [main]})
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    r = await client.put(f"/api/settings/rbac/roles/{rid}", headers=tenant, json={
        "name": "Main only", "location_scope": "selected", "location_ids": []})
    assert r.status_code == 422, r.text
    # another shop's location (or a made-up id) is not a location of this shop
    other = await _branch(db, me["org_id"], "tmp")
    from backend.models import Location
    loc = (await db.execute(select(Location).where(Location.id == other))).scalar_one()
    await db.delete(loc)
    await db.commit()
    r = await client.put(f"/api/settings/rbac/roles/{rid}", headers=tenant, json={
        "name": "Main only", "location_scope": "selected", "location_ids": ["no-such-location"]})
    assert r.status_code == 422, r.text
    # "all" with no locations is fine
    r = await client.put(f"/api/settings/rbac/roles/{rid}", headers=tenant, json={
        "name": "Main only", "location_scope": "all", "location_ids": []})
    assert r.status_code == 200, r.text


# ── SEC-04: warehouse scope on the inventory reports ─────────────────────────────────────────────

async def _wh_setup(client, tenant, db):
    from backend.routers import reports
    from backend.tests import kit_helpers as K
    reports._dash_cache.clear()
    me = await _me(client, tenant)
    main = await K.loc(client, tenant)
    b = await _branch(db, me["org_id"])
    pid = await K.part(client, tenant, main, "Screen", 99, 10, qty=5)
    r = await client.post(f"/api/inventory/parts/{pid}/adjust", headers=tenant, json={
        "location_id": b, "qty": 2, "movement_type": "receive", "unit_cost": 10})
    assert r.status_code == 200, r.text
    from backend.models import Part
    p = (await db.execute(select(Part).where(Part.id == pid))).scalar_one()
    p.min_stock = 3
    await db.commit()
    H, _e, _r = await _scoped_login(client, db, me["org_id"], email=f"wh-{uuid.uuid4().hex[:6]}@x.dev",
                                    permissions=REPORT_PERMS, perms_explicit=True,
                                    warehouse_scope="selected", warehouse_ids=[b])
    return main, b, pid, H


async def _run(client, H, key):
    r = await client.get(f"/api/reports/run/{key}", headers=H)
    assert r.status_code == 200, r.text
    return r.json()["rows"]


async def test_inventory_reports_show_only_reachable_warehouses(client, tenant, db):
    main, b, pid, H = await _wh_setup(client, tenant, db)
    assert (await _run(client, tenant, "stock_levels"))[0][2] == 7
    assert (await _run(client, H, "stock_levels"))[0][2] == 2                 # was 7
    own_val = await _run(client, tenant, "stock_valuation")
    assert own_val[-1][2] == 7
    mine_val = await _run(client, H, "stock_valuation")
    assert mine_val[-1][2] == 2 and len(mine_val) == 2                         # B + TOTAL
    assert sum(r[4] for r in await _run(client, H, "assortment")) == 2
    # product turnover / write-offs: a write-off at the main branch isn't B's
    from backend.models import StockMovement
    db.add(StockMovement(part_id=pid, location_id=main, qty=-1, movement_type="writeoff"))
    await db.commit()
    assert len(await _run(client, tenant, "inventory_writeoffs")) == 1
    assert await _run(client, H, "inventory_writeoffs") == []
    # purchase list: B is short (2 free < 3 min), the shop as a whole (7) is not
    assert await _run(client, tenant, "purchase_list") == []
    assert [r[0] for r in await _run(client, H, "purchase_list")] == ["Screen"]


async def test_low_stock_tiles_count_only_reachable_stock(client, tenant, db):
    main, b, pid, H = await _wh_setup(client, tenant, db)
    assert (await client.get("/api/reports/inventory/low-stock", headers=tenant)).json()["count"] == 0
    mine = (await client.get("/api/reports/inventory/low-stock", headers=H)).json()
    assert mine["count"] == 1 and mine["items"][0]["qty"] == 2
    assert (await client.get("/api/reports/dashboard", headers=H)).json()["stats"]["low_stock_parts"] == 1
    assert (await client.get("/api/reports/dashboard", headers=tenant)).json()["stats"]["low_stock_parts"] == 0


# ── PERF-12: SQL aggregation gives the same figures as the old Python loops ─────────────────────

async def _old_technicians(db, org, df, dt):
    """The pre-PERF-12 builder, verbatim (minus the location filter it never had)."""
    from backend.models import Employee, OrderLine
    from backend.services import revenue as money_rules
    from backend.services.money import q2
    from backend.services.payroll import _line_splits
    from backend.services.revenue import line_net
    dropped = await money_rules.dropped_keys(db, org)
    lines = (await db.execute(
        select(OrderLine, Order).join(Order, OrderLine.order_id == Order.id)
        .where(Order.org_id == org, Order.created_at >= df, Order.created_at <= dt,
               Order.is_estimate == False, Order.archived == False,     # noqa: E712
               *([Order.status.notin_(dropped)] if dropped else [])))).all()
    emp = {e.id: e.name for e in (await db.execute(select(Employee).where(Employee.org_id == org))).scalars().all()}
    agg = {}
    for line, order in lines:
        for tid, share in _line_splits(line, fallback=order.assigned_to_id):
            a = agg.setdefault(tid, {"rev": 0, "cost": 0, "n": set()})
            a["rev"] += line_net(line.total, line.tax_rate) * share
            a["cost"] += (line.cost_price or 0) * (line.qty or 0) * share
            a["n"].add(order.id)
    return [[emp.get(tid, "—"), len(v["n"]), q2(v["rev"]), q2(v["rev"] - v["cost"])] for tid, v in agg.items()]


async def _people(db, org, n=3):
    from backend.models import Employee
    out = []
    for i in range(n):
        e = Employee(org_id=org, name=f"Tech {i}", email=f"t{i}-{uuid.uuid4().hex[:6]}@x.dev", password_hash="x", active=True)
        db.add(e)
        out.append(e)
    await db.commit()
    return [e.id for e in out]


async def test_technicians_report_matches_the_python_split(client, tenant, db):
    from backend.models import OrderLine
    from backend.routers import reports
    me = await _me(client, tenant)
    org = me["org_id"]
    main = await _loc(client, tenant)
    t0, t1, t2 = await _people(db, org)
    jobs = [await _job(client, tenant, main, 10, close=False) for _ in range(4)]
    o = (await db.execute(select(Order).where(Order.id == jobs[3]))).scalar_one()
    o.assigned_to_id = t2                                      # the fallback technician
    await db.commit()
    specs = [
        (jobs[0], dict(technician_id=t0, total=121, tax_rate=21, cost_price=10, qty=2)),
        (jobs[0], dict(technician_id=t0, work_share=0.5, total=50, cost_price=5, qty=1)),
        (jobs[0], dict(co_technicians=[{"employee_id": t0, "share": 0.25}, {"employee_id": t1, "share": 0.75}],
                       total=200, tax_rate=10, cost_price=40, qty=1)),
        (jobs[1], dict(co_technicians=[{"employee_id": t1, "share": 1}], total=80, cost_price=0, qty=3)),
        (jobs[1], dict(technician_id=t1, total=33.33, cost_price=1.5, qty=4, work_share=0)),
        (jobs[2], dict(co_technicians=[{"employee_id": None, "share": 1}], technician_id=t0, total=70, qty=1)),
        (jobs[3], dict(total=90, cost_price=30, qty=1)),                         # → the assignee
        (jobs[3], dict(co_technicians=None, total=15, cost_price=2, qty=2, tax_rate=5)),
    ]
    for oid, kw in specs:
        db.add(OrderLine(org_id=org, order_id=oid, line_type="labour", kind="service", description="x",
                         unit_price=kw.get("total", 0), **kw))
    await db.commit()
    df, dt = reports._parse_range(None, None, None)
    old = await _old_technicians(db, org, df, dt)
    _cols, new = await reports._rep_technicians(db, org, df, dt)
    assert sorted(map(tuple, new)) == sorted(map(tuple, old))
    assert {r[0] for r in new} >= {"Tech 0", "Tech 1", "Tech 2"}


async def _old_cash_flow(db, org, df, dt):
    from backend.models import CashFlowItem, Transaction
    from backend.services import revenue as money_rules
    from backend.services.money import q2
    txns = (await db.execute(select(Transaction).where(
        Transaction.org_id == org, func.coalesce(Transaction.status, "active") != "deleted",
        Transaction.actual_date >= df, Transaction.actual_date <= dt))).scalars().all()
    items = {c.id: c.name for c in (await db.execute(select(CashFlowItem).where(CashFlowItem.org_id == org))).scalars().all()}
    agg = {}
    for t in txns:
        kind = "refund" if t.txn_type == "expense" and t.ref_type in money_rules.CUSTOMER_REF_TYPES else t.txn_type
        name = items.get(t.cash_flow_item_id, kind)
        agg[name] = agg.get(name, 0) + t.amount
    return [[k, q2(v)] for k, v in sorted(agg.items(), key=lambda x: -x[1])]


async def test_cash_flow_report_matches_the_python_sum(client, tenant, db):
    from backend.models import Account, CashFlowItem, Transaction
    from backend.routers import reports
    org = (await _me(client, tenant))["org_id"]
    a = Account(org_id=org, name="Till", payment_option="cash")
    rent, sales = CashFlowItem(org_id=org, name="Rent"), CashFlowItem(org_id=org, name="Sales")
    db.add_all([a, rent, sales])
    await db.flush()
    for kw in (dict(txn_type="income", amount=100.10, cash_flow_item_id=sales.id),
               dict(txn_type="income", amount=50.05, cash_flow_item_id=sales.id),
               dict(txn_type="expense", amount=-500, cash_flow_item_id=rent.id),
               dict(txn_type="expense", amount=-20, ref_type="order"),
               dict(txn_type="expense", amount=-7.5, ref_type="sale", cash_flow_item_id=None),
               dict(txn_type="expense", amount=-3, ref_type="manual"),
               dict(txn_type="income", amount=999, status="deleted"),
               dict(txn_type="fee", amount=-1.25)):
        db.add(Transaction(org_id=org, account_id=a.id, **kw))
    await db.commit()
    df, dt = reports._parse_range(None, None, None)
    _c, new = await reports._rep_cash_flow(db, org, df, dt)
    assert new == await _old_cash_flow(db, org, df, dt)
    assert ["refund", -27.5] in new and ["Sales", 150.15] in new


async def _old_labour(db, org, df, dt, scope=None):
    """utilization, by-job and cost totals as the pre-PERF-12 loops computed them."""
    from backend.models import Employee, TimeEntry
    from backend.services.money import q2
    rows = (await db.execute(select(TimeEntry).where(
        TimeEntry.org_id == org, TimeEntry.state != "void",
        TimeEntry.started_at >= df, TimeEntry.started_at < dt))).scalars().all()
    if scope is not None:
        rows = [e for e in rows if e.employee_id in scope]
    emps = {e.id: e.name for e in (await db.execute(select(Employee).where(Employee.org_id == org))).scalars().all()}
    agg = {}
    for e in rows:
        a = agg.setdefault(e.employee_id, {"clocked": 0, "billable": 0, "job": 0, "cost": 0.0})
        a["clocked"] += e.duration_sec
        if e.billable:
            a["billable"] += e.duration_sec
        if e.order_id:
            a["job"] += e.duration_sec
        a["cost"] += e.duration_sec / 3600 * (e.cost_rate or 0)
    util = []
    for eid, a in agg.items():
        u = round(a["billable"] / a["clocked"] * 100, 1) if a["clocked"] else 0
        util.append([emps.get(eid, eid), q2(a["clocked"] / 3600), q2(a["billable"] / 3600),
                     q2(a["job"] / 3600), u, q2(a["cost"])])
    by_order = {}
    for e in rows:
        if not e.order_id:
            continue
        a = by_order.setdefault(e.order_id, {"sec": 0, "cost": 0.0, "bill": 0.0})
        a["sec"] += e.duration_sec
        a["cost"] += e.duration_sec / 3600 * (e.cost_rate or 0)
        if e.billable:
            a["bill"] += e.duration_sec / 3600 * (e.billable_rate or 0)
    total = sum(e.duration_sec for e in rows)
    billable = sum(e.duration_sec for e in rows if e.billable)
    cost = sum(e.duration_sec / 3600 * (e.cost_rate or 0) for e in rows)
    bill = sum(e.duration_sec / 3600 * (e.billable_rate or 0) for e in rows if e.billable)
    totals = [q2(total / 3600), q2(billable / 3600), q2(cost), q2(bill),
              round(billable / total * 100, 1) if total else 0]
    return util, {k: (q2(v["sec"] / 3600), q2(v["cost"]), q2(v["bill"])) for k, v in by_order.items()}, totals


async def test_labour_reports_match_the_python_sums(client, tenant, db):
    from backend.models import TimeEntry
    from backend.routers import reports
    from backend.services.dates import now_utc
    org = (await _me(client, tenant))["org_id"]
    main = await _loc(client, tenant)
    t0, t1, _t2 = await _people(db, org)
    j1 = await _job(client, tenant, main, 10, close=False)
    j2 = await _job(client, tenant, main, 10, close=False)
    start = now_utc() - timedelta(days=2)
    for i, (emp, oid, sec, billable, cost, bill, state) in enumerate([
            (t0, j1, 3600, True, 25, 60, "approved"), (t0, j1, 1800, False, 25, 60, "approved"),
            (t0, None, 7200, True, 25.5, 0, "approved"), (t1, j2, 1234, True, 33.33, 77.7, "approved"),
            (t1, j1, 999, True, 0, 50, "approved"), (t1, j2, 4000, True, 40, 90, "void"),
            (t0, j2, 36000, True, 20, 45, "approved")]):
        db.add(TimeEntry(org_id=org, employee_id=emp, order_id=oid, started_at=start + timedelta(minutes=i),
                         ended_at=start + timedelta(minutes=i, seconds=sec), duration_sec=sec, billable=billable,
                         cost_rate=cost, billable_rate=bill, state=state, location_id=main))
    await db.commit()
    df, dt = reports._parse_range(None, None, None)
    util, by_order, totals = await _old_labour(db, org, df, dt)
    _c, new_util = await reports._rep_labor_utilization(db, org, df, dt)
    assert sorted(map(tuple, new_util)) == sorted(map(tuple, util))
    _c, new_by_job = await reports._rep_labor_by_job(db, org, df, dt)
    nums = dict((await db.execute(select(Order.id, Order.order_number).where(Order.id.in_([j1, j2])))).all())
    assert {r[0]: (r[2], r[3], r[4]) for r in new_by_job} == {nums[k]: v for k, v in by_order.items()}
    _c, cost_rows = await reports._rep_labor_cost(db, org, df, dt)
    got = dict((r[0], r[1]) for r in cost_rows)
    assert [got["Total hours"], got["Billable hours"], got["Labor cost"], got["Billable value"],
            got["Utilization %"]] == totals
    assert got["Overtime hours (>8h/day)"] == 5.5                   # t0: 13.5h in one day
    # the payroll scope still narrows the rows before they are added up
    util_s, _bo, totals_s = await _old_labour(db, org, df, dt, scope={t1})
    _c, new_s = await reports._rep_labor_utilization(db, org, df, dt, payroll_ids={t1})
    assert sorted(map(tuple, new_s)) == sorted(map(tuple, util_s))
    _c, cost_s = await reports._rep_labor_cost(db, org, df, dt, payroll_ids={t1})
    assert dict((r[0], r[1]) for r in cost_s)["Total hours"] == totals_s[0]


# ── PERF-12: exports are written row by row, pages are opt-in ───────────────────────────────────

async def test_xlsx_export_is_streamed_and_has_every_row(client, tenant, db):
    import openpyxl
    loc = await _loc(client, tenant)
    for p in (10, 20, 30):
        await _job(client, tenant, loc, p)
    run = (await client.get("/api/reports/run/wo_closed", headers=tenant)).json()
    r = await client.get("/api/reports/export/wo_closed", headers=tenant)
    assert r.status_code == 200, r.text
    wb = openpyxl.load_workbook(io.BytesIO(r.content))
    ws = wb.active
    values = [list(row) for row in ws.iter_rows(values_only=True)]
    assert values[0][0] == "Work orders closed"
    assert values[3] == run["columns"]
    assert ws.cell(row=4, column=1).font.bold
    assert [list(v) for v in values[4:]] == [list(x) for x in run["rows"]]
    c = await client.get("/api/reports/export-csv/wo_closed", headers=tenant)
    assert c.status_code == 200
    assert len(c.text.strip().splitlines()) == 1 + len(run["rows"])


async def test_appointments_list_takes_an_optional_page(client, tenant, db):
    from backend.models import Appointment
    from backend.services.dates import now_utc
    org = (await _me(client, tenant))["org_id"]
    for i in range(5):
        db.add(Appointment(org_id=org, scheduled_for=now_utc() + timedelta(hours=i), duration_min=30))
    await db.commit()
    full = (await client.get("/api/appointments", headers=tenant)).json()
    assert len(full["items"]) == 5                                   # default unchanged: everything
    page = (await client.get("/api/appointments?limit=2&skip=2", headers=tenant)).json()
    assert [a["id"] for a in page["items"]] == [a["id"] for a in full["items"][2:4]]
    assert page["total"] == 5


async def test_products_list_takes_an_optional_page(client, tenant, db):
    from backend.tests import kit_helpers as K
    loc = await K.loc(client, tenant)
    for n in ("A", "B", "C", "D"):
        await K.part(client, tenant, loc, f"Part {n}", 10, 5, qty=1)
    full = (await client.get("/api/catalog/products", headers=tenant)).json()
    assert len(full["items"]) == 4
    page = (await client.get("/api/catalog/products?limit=2&skip=1", headers=tenant)).json()
    assert [p["id"] for p in page["items"]] == [p["id"] for p in full["items"][1:3]]
    assert page["total"] == 4


# ── RPT-06: the accountant export's last shop day runs to its last microsecond ──────────────────

async def test_accountant_export_includes_the_last_instant_of_the_day(client, tenant, db):
    from backend.models import Account, Transaction
    from backend.services.dates import day_end_utc, shop_tzname, today_in
    org = (await _me(client, tenant))["org_id"]
    tz = await shop_tzname(db, org)
    day = today_in(tz) - timedelta(days=3)
    a = Account(org_id=org, name="Till", payment_option="cash")
    db.add(a)
    await db.flush()
    db.add(Transaction(org_id=org, account_id=a.id, txn_type="income", amount=12.34, comment="late",
                       actual_date=day_end_utc(day, tz) - timedelta(milliseconds=500)))
    db.add(Transaction(org_id=org, account_id=a.id, txn_type="income", amount=1, comment="next day",
                       actual_date=day_end_utc(day, tz)))
    await db.commit()
    r = await client.get(f"/api/finance/export/accountant.csv?date_from={day}&date_to={day}", headers=tenant)
    assert r.status_code == 200, r.text
    assert "late" in r.text                                           # was missing: 23:59:59.5
    assert "next day" not in r.text
