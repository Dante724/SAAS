"""P1 audit 2026-11-12: reports and dashboards (PERF-07, PERF-08, PERF-11, PERF-12, PERF-13, PERF-15,
RPT-02, RPT-03, RPT-05, RPT-06, RPT-07, RPT-08).

Statement counts are measured the way the audit probes did: every SQL statement the endpoint sends.
"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import event, select

from backend.models import Order, Organisation


class _Sql:
    def __init__(self, engine):
        self.engine, self.sql = engine, []

    def _hit(self, conn, cur, stmt, params, ctx, many):
        self.sql.append(stmt)

    def __enter__(self):
        event.listen(self.engine, "before_cursor_execute", self._hit)
        return self

    def __exit__(self, *exc):
        event.remove(self.engine, "before_cursor_execute", self._hit)

    @property
    def n(self):
        return len(self.sql)


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
        "line_type": "labour", "kind": "service", "description": "Fix", "qty": 1, "unit_price": price})
    assert r.status_code in (200, 201), r.text
    if close:
        r = await client.patch(f"/api/orders/{oid}/status", json={"status": await _closed_key(client, H)}, headers=H)
        assert r.status_code == 200, r.text
    return oid


# ── PERF-07 / RPT-08: Home dashboard ───────────────────────────────────────────────────────────────

async def test_home_average_job_is_this_months_not_all_time(client, tenant, db):
    """RPT-08: "This month › Average job" averaged every closed job the shop ever had."""
    loc = await _loc(client, tenant)
    old = await _job(client, tenant, loc, 1000)
    await _job(client, tenant, loc, 100)
    o = (await db.execute(select(Order).where(Order.id == old))).scalar_one()
    o.closed_at = o.closed_at - timedelta(days=70)        # earned two months ago
    o.created_at = o.closed_at
    await db.commit()
    r = await client.get("/api/reports/company-dashboard", headers=tenant)
    assert r.status_code == 200, r.text
    assert r.json()["monthly"]["avg_work_order"] == 100


async def test_home_dashboard_statement_count_is_bounded_and_cached(client, tenant, db, monkeypatch):
    """PERF-07: 65 statements on every Home load, uncached."""
    from backend.config import settings
    from backend.routers import reports
    loc = await _loc(client, tenant)
    for p in (100, 200, 300):
        await _job(client, tenant, loc, p)
    await _job(client, tenant, loc, 50, close=False)
    reports._dash_cache.clear()
    monkeypatch.setattr(settings, "HOME_DASHBOARD_CACHE_S", 0)
    with _Sql(db.bind.sync_engine) as cold:
        first = (await client.get("/api/reports/company-dashboard", headers=tenant)).json()
    assert cold.n <= 45, cold.n

    monkeypatch.setattr(settings, "HOME_DASHBOARD_CACHE_S", 30)
    await client.get("/api/reports/company-dashboard", headers=tenant)       # fills the cache
    with _Sql(db.bind.sync_engine) as warm:
        again = (await client.get("/api/reports/company-dashboard", headers=tenant)).json()
    assert again == first
    assert warm.n <= 12, warm.n
    reports._dash_cache.clear()


async def test_home_dashboard_cache_keeps_role_redaction_and_location(client, tenant, db, monkeypatch):
    """The cached payload is shop-wide; money is hidden per caller after the cache, and a branch
    gets its own entry."""
    from backend.config import settings
    from backend.models import Employee, Location, Role
    from backend.routers import reports
    monkeypatch.setattr(settings, "HOME_DASHBOARD_CACHE_S", 30)
    reports._dash_cache.clear()
    loc = await _loc(client, tenant)
    await _job(client, tenant, loc, 100)
    owner = (await client.get("/api/reports/company-dashboard", headers=tenant)).json()
    assert owner["today"]["revenue"] is not None

    org_id = (await client.get("/api/auth/me", headers=tenant)).json()["org_id"]
    loc2 = Location(org_id=org_id, name="Branch 2")
    role = Role(org_id=org_id, name="Counter", permissions={
        "can_view_reports": True, "can_view_finance": False, "can_view_balance": False})
    db.add_all([loc2, role])
    await db.commit()
    import uuid
    email = f"c-{uuid.uuid4().hex[:6]}@test.dev"
    r = await client.post("/api/staff/employees", headers=tenant, json={
        "name": "Counter", "email": email, "password": "pw-test-12345", "role_id": role.id})
    assert r.status_code == 201, r.text
    tok = (await client.post("/api/auth/token", data={"username": email, "password": "pw-test-12345"})).json()
    H2 = {"Authorization": f"Bearer {tok['access_token']}"}
    staff = (await client.get("/api/reports/company-dashboard", headers=H2)).json()
    assert staff["today"]["revenue"] is None and staff["kpis"]["total_money"] is None
    assert staff["today"]["work_orders"] == owner["today"]["work_orders"]

    branch = (await client.get(f"/api/reports/company-dashboard?location_id={loc2.id}", headers=tenant)).json()
    assert branch["today"]["work_orders"] == 0
    assert (await db.execute(select(Employee).where(Employee.email == email))).scalar_one()
    reports._dash_cache.clear()


# ── PERF-08: invoice shares are scoped to the shop ─────────────────────────────────────────────────

async def test_invoice_job_shares_never_scan_other_shops_links(client, tenant, db):
    loc = await _loc(client, tenant)
    await _job(client, tenant, loc, 100)
    with _Sql(db.bind.sync_engine) as c:
        r = await client.get("/api/reports/company-dashboard", headers=tenant)
    assert r.status_code == 200
    import re
    # every read of the link table is joined to (or correlated with) this shop's invoices
    ok = r"FROM invoice_orders(\s+JOIN invoices|\s+WHERE invoice_orders.invoice_id = invoices.id)"
    unscoped = [s for s in c.sql if re.search(r"FROM invoice_orders", s) and
                len(re.findall(r"FROM invoice_orders", s)) != len(re.findall(ok, s))]
    assert not unscoped, unscoped[0][:400]


# ── PERF-11: no query per open PO / per location ───────────────────────────────────────────────────

async def _po(client, H, loc, sup, pid):
    r = await client.post("/api/inventory/po", headers=H, json={
        "supplier_id": sup, "location_id": loc, "lines": [{"part_id": pid, "qty_ordered": 5, "unit_cost": 20}]})
    assert r.status_code in (200, 201), r.text


@pytest.mark.parametrize("key", ["purchase_list", "reorder_forecast"])
async def test_po_reports_are_flat_in_open_pos(client, tenant, db, key):
    loc = await _loc(client, tenant)
    sup = (await client.post("/api/suppliers", json={"name": "Sup"}, headers=tenant)).json()["id"]
    pid = (await client.post("/api/inventory/parts", headers=tenant, json={
        "name": "P", "sku": "P1", "min_stock": 50, "cost_price": 2})).json()["id"]
    await client.post(f"/api/inventory/parts/{pid}/adjust", headers=tenant,
                      json={"location_id": loc, "qty": 3, "movement_type": "receive"})
    await client.post("/api/sales", headers=tenant, json={"lines": [{
        "kind": "product", "ref_id": pid, "description": "P", "qty": 3, "unit_price": 5}], "payment_method": "cash"})
    from backend.models import Part, Supplier
    (await db.execute(select(Part).where(Part.id == pid))).scalar_one().default_supplier_id = sup
    (await db.execute(select(Supplier).where(Supplier.id == sup))).scalar_one().lead_days = 300
    await db.commit()
    await _po(client, tenant, loc, sup, pid)
    with _Sql(db.bind.sync_engine) as few:
        r1 = (await client.get(f"/api/reports/run/{key}", headers=tenant)).json()
    for _ in range(4):
        await _po(client, tenant, loc, sup, pid)
    with _Sql(db.bind.sync_engine) as many:
        r2 = (await client.get(f"/api/reports/run/{key}", headers=tenant)).json()
    assert many.n == few.n, (few.n, many.n)
    on_order = r2["columns"].index("On order")
    assert r1["rows"][0][on_order] == 5 and r2["rows"][0][on_order] == 25


async def test_location_comparison_is_flat_in_locations_and_ties_out(client, tenant, db):
    from backend.models import Location
    loc = await _loc(client, tenant)
    await _job(client, tenant, loc, 100)
    with _Sql(db.bind.sync_engine) as one:
        a = (await client.get("/api/analytics/location-comparison", headers=tenant)).json()
    org_id = (await client.get("/api/auth/me", headers=tenant)).json()["org_id"]
    extra = [Location(org_id=org_id, name=f"B{i}") for i in range(3)]
    db.add_all(extra)
    await db.commit()
    for lo in extra:
        await _job(client, tenant, lo.id, 10)
    with _Sql(db.bind.sync_engine) as four:
        b = (await client.get("/api/analytics/location-comparison", headers=tenant)).json()
    assert four.n <= one.n + 2, (one.n, four.n)
    by = {r["location_id"]: r["revenue"] for r in b["locations"]}
    assert by[loc] == a["locations"][0]["revenue"] == 100
    assert all(by[lo.id] == 10 for lo in extra)
    # each row agrees with the per-location net sales every other report uses
    from backend.services import revenue
    start = datetime.utcnow() - timedelta(days=30)
    end = datetime.utcnow()
    for lid in (loc, *[lo.id for lo in extra]):
        assert by[lid] == (await revenue.net_sales(db, org_id, start, end, lid))["revenue"]


# ── PERF-12: report windows are bounded ────────────────────────────────────────────────────────────

async def test_report_span_is_capped(client, tenant):
    r = await client.get("/api/reports/run/wo_created?date_from=2020-01-01&date_to=2026-01-01", headers=tenant)
    assert r.status_code == 422
    r = await client.get("/api/reports/export-csv/wo_created?date_from=2020-01-01&date_to=2026-01-01", headers=tenant)
    assert r.status_code == 422
    r = await client.get("/api/reports/run/wo_created?date_from=2025-01-01&date_to=2026-01-01", headers=tenant)
    assert r.status_code == 200


# ── PERF-13 / RPT-02: Insights ─────────────────────────────────────────────────────────────────────

async def test_insights_payments_filter_dates_in_sql_and_match_paid(client, tenant, db):
    from backend.models import Payment
    loc = await _loc(client, tenant)
    acc = (await client.post("/api/cash/accounts", json={"name": "Card", "payment_option": "card"},
                             headers=tenant)).json()["id"]
    oid = await _job(client, tenant, loc, 100, close=False)
    r = await client.post(f"/api/cash/orders/{oid}/pay", headers=tenant,
                          json={"method": "card", "account_id": acc, "amount": 100})
    assert r.status_code in (200, 201), r.text
    old = (await db.execute(select(Payment).where(Payment.order_id == oid))).scalars().first()
    db.add(Payment(order_id=oid, account_id=acc, amount=999, method="card", kind="payment",
                   paid_at=old.paid_at - timedelta(days=800)))
    db.add(Payment(order_id=oid, account_id=acc, amount=7, method="balance", kind="payment",
                   paid_at=old.paid_at))
    await db.commit()
    with _Sql(db.bind.sync_engine) as c:
        r = await client.get("/api/reports/insights/data?subject=payments&kind=diagram&metric=amount&period=30d",
                             headers=tenant)
    assert r.status_code == 200, r.text
    assert sum(r.json()["values"]) == 100          # not the 800-day-old one, not the balance one
    pay_sql = [s for s in c.sql if "FROM payments" in s]
    assert pay_sql and all("paid_at >=" in s for s in pay_sql), pay_sql


async def _kolkata(db, client, H):
    org_id = (await client.get("/api/auth/me", headers=H)).json()["org_id"]
    org = (await db.execute(select(Organisation).where(Organisation.id == org_id))).scalar_one()
    org.timezone = "Asia/Kolkata"
    await db.commit()
    return org_id


async def test_insights_trend_buckets_by_shop_day(client, tenant, db):
    """RPT-02: a sale at 20:00 UTC is 01:30 the next day in Kolkata; it was bucketed on the UTC day."""
    from backend.models import Sale
    from backend.services.dates import utc_to_wall
    await _kolkata(db, client, tenant)
    r = await client.post("/api/sales", headers=tenant, json={"lines": [{
        "kind": "service", "description": "X", "qty": 1, "unit_price": 40}], "payment_method": "cash"})
    assert r.status_code == 201, r.text
    s = (await db.execute(select(Sale))).scalars().first()
    now = datetime.utcnow()
    at = datetime(now.year, now.month, now.day, 20, 0) - timedelta(days=2)
    s.created_at = at
    await db.commit()
    d = (await client.get("/api/reports/insights/data?subject=sales&kind=trend&metric=count&period=30d",
                          headers=tenant)).json()
    local_label = utc_to_wall(at, "Asia/Kolkata").strftime("%d %b")
    assert d["values"][d["labels"].index(local_label)] == 1


async def test_insights_closed_orders_counts_jobs_closed_in_the_period(client, tenant, db):
    """RPT-02: "closed orders" counted jobs *created* in the period that are closed now."""
    loc = await _loc(client, tenant)
    oid = await _job(client, tenant, loc, 100)
    o = (await db.execute(select(Order).where(Order.id == oid))).scalar_one()
    o.created_at = o.created_at - timedelta(days=60)      # opened two months ago, closed today
    await db.commit()
    d = (await client.get("/api/reports/insights/data?subject=closed_orders&kind=diagram&metric=count&period=30d"
                          "&group=location", headers=tenant)).json()
    assert sum(d["values"]) == 1


# ── RPT-03: analytics trends by shop day ───────────────────────────────────────────────────────────

async def test_analytics_trends_use_shop_days(client, tenant, db):
    await _kolkata(db, client, tenant)
    loc = await _loc(client, tenant)
    oid = await _job(client, tenant, loc, 100, close=False)
    now = datetime.utcnow()
    at = datetime(now.year, now.month, now.day, 20, 0) - timedelta(days=2)
    o = (await db.execute(select(Order).where(Order.id == oid))).scalar_one()
    o.created_at = at
    await db.commit()
    series = (await client.get("/api/analytics/trends?days=30", headers=tenant)).json()["series"]
    from backend.services.dates import utc_to_wall
    day = utc_to_wall(at, "Asia/Kolkata").date().isoformat()
    assert any(r["date"] == day and r["created"] == 1 for r in series), series


# ── RPT-05 / RPT-06: report cells in local time; the last instant of the day counts ────────────────

async def test_report_cells_print_shop_local_time(client, tenant, db):
    await _kolkata(db, client, tenant)
    loc = await _loc(client, tenant)
    oid = await _job(client, tenant, loc, 100, close=False)
    o = (await db.execute(select(Order).where(Order.id == oid))).scalar_one()
    now = datetime.utcnow()
    o.created_at = datetime(now.year, now.month, now.day, 2, 10) - timedelta(days=1)
    await db.commit()
    d = (await client.get("/api/reports/run/wo_created", headers=tenant)).json()
    cells = [c for row in d["rows"] for c in row if isinstance(c, str)]
    from backend.services.dates import utc_to_wall
    local = utc_to_wall(o.created_at, "Asia/Kolkata").strftime("%Y-%m-%d %H:%M")
    assert local in cells, cells


async def test_report_window_includes_the_last_second_of_the_day(client, tenant, db):
    from backend.services.dates import day_end_utc, shop_tzname
    loc = await _loc(client, tenant)
    oid = await _job(client, tenant, loc, 100, close=False)
    org_id = (await client.get("/api/auth/me", headers=tenant)).json()["org_id"]
    tz = await shop_tzname(db, org_id)
    day = datetime.utcnow().date() - timedelta(days=3)
    o = (await db.execute(select(Order).where(Order.id == oid))).scalar_one()
    o.created_at = day_end_utc(day, tz) - timedelta(microseconds=500_000)    # 23:59:59.5 local
    await db.commit()
    d = (await client.get(f"/api/reports/run/wo_created?date_from={day}&date_to={day}", headers=tenant)).json()
    assert d["row_count"] == 1


# ── RPT-07: orders list date filters are shop days ─────────────────────────────────────────────────

async def test_orders_list_created_to_covers_that_whole_shop_day(client, tenant, db):
    from backend.services.dates import shop_today
    loc = await _loc(client, tenant)
    await _job(client, tenant, loc, 100, close=False)
    org_id = (await client.get("/api/auth/me", headers=tenant)).json()["org_id"]
    today = await shop_today(db, org_id)
    r = await client.get(f"/api/orders?created_from={today}&created_to={today}", headers=tenant)
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 1


# ── PERF-15: id lists above the bind-parameter limit ───────────────────────────────────────────────

async def test_job_figures_takes_more_ids_than_the_parameter_limit(client, tenant, db):
    from backend.services import revenue
    loc = await _loc(client, tenant)
    oid = await _job(client, tenant, loc, 100)
    ids = [f"missing-{i}" for i in range(40_000)] + [oid]
    figs = await revenue.job_figures(db, ids)
    assert figs[oid]["revenue"] == 100
    assert len(figs) == len(ids)
