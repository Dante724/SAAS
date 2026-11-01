"""Workstream B — Total profit (gap #1) and Unsuccessful jobs profit (gap #2).

One shared profit function (services/profit.job_figures) feeds Job profitability, Total profit
and the dropped-jobs report, so the numbers can't disagree. These tests build a small shop:

  Job A  closed   1 × 100 + 10% tax, parts cost 30, 30 min labour @ 60/h, commission 5 → profit 35
  Job B  dropped  1 × 200, parts cost 50, paid 40 (160 written off)                       → profit −10
  Sale   counter  80, cost 20                                                              → profit 60
  Voided sale and an open job don't count.
"""
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

pytestmark = pytest.mark.asyncio


async def _loc(client, H):
    d = (await client.get("/api/staff/locations", headers=H)).json()
    return (d if isinstance(d, list) else d["items"])[0]["id"]


async def _me(client, H):
    return (await client.get("/api/auth/me", headers=H)).json()


async def _job(client, H, *, price, cost, tax_rate=0.0, loc=None, assignee=None):
    cid = (await client.post("/api/clients", json={"name": f"C {uuid.uuid4().hex[:4]}"}, headers=H)).json()["id"]
    body = {"client_id": cid, "complaint": "Broken"}
    if loc:
        body["location_id"] = loc
    if assignee:
        body["assigned_to_id"] = assignee
    o = (await client.post("/api/orders", json=body, headers=H)).json()
    r = await client.post(f"/api/orders/{o['id']}/lines", json={
        "line_type": "part", "kind": "product", "description": "Part", "qty": 1,
        "unit_price": price, "cost_price": cost, "tax_rate": tax_rate, "tax_mode": "imposed"}, headers=H)
    assert r.status_code in (200, 201), r.text
    return o["id"]


async def _world(client, H, db):
    from backend.models import EmployeeBalanceLedger, Order, Payment, Sale, TimeEntry
    me = await _me(client, H)
    loc = await _loc(client, H)
    a = await _job(client, H, price=100, cost=30, tax_rate=10)
    b = await _job(client, H, price=200, cost=50)
    await _job(client, H, price=999, cost=1)            # stays open
    org = (await db.execute(select(Order.org_id).where(Order.id == a))).scalar()
    emp = me.get("id") or me.get("employee", {}).get("id")
    now = datetime.utcnow()
    db.add(TimeEntry(org_id=org, employee_id=emp, order_id=a, category="job", started_at=now - timedelta(hours=2),
                     ended_at=now - timedelta(hours=1, minutes=30), duration_sec=1800, cost_rate=60,
                     state="stopped", source="manual"))
    db.add(EmployeeBalanceLedger(org_id=org, employee_id=emp, entry_type="commission", amount=5,
                                 event="order_closed", ref_type="order", ref_id=a,
                                 idempotency_key=f"t:{a}", accrual_key=f"t:{a}"))
    db.add(Payment(order_id=b, amount=40, method="cash", kind="payment"))
    db.add(Sale(org_id=org, location_id=loc, sale_number="S-1", seller_id=emp, total=80, total_cost=20, paid=True))
    db.add(Sale(org_id=org, location_id=loc, sale_number="S-2", seller_id=emp, total=500, total_cost=1,
                paid=True, voided_at=now))
    await db.commit()
    assert (await client.patch(f"/api/orders/{a}/status", json={"status": "done"}, headers=H)).status_code == 200
    r = await client.patch(f"/api/orders/{b}/status", json={"status": "dropped", "reason": "Too expensive"}, headers=H)
    assert r.status_code == 200, r.text
    return {"a": a, "b": b, "org": org, "emp": emp, "loc": loc}


async def test_total_profit_combines_jobs_dropped_jobs_and_sales(client, tenant, db):
    w = await _world(client, tenant, db)
    r = await client.get("/api/reports/total-profit", headers=tenant)
    assert r.status_code == 200, r.text
    rep = r.json()
    jobs, dropped, sales = rep["buckets"]["jobs"], rep["buckets"]["dropped"], rep["buckets"]["sales"]
    assert jobs == {**jobs, "count": 1, "revenue": 100.0, "tax": 10.0, "cogs": 30.0,
                    "labour_cost": 30.0, "commission": 5.0, "profit": 35.0}
    assert dropped == {**dropped, "count": 1, "revenue": 40.0, "cogs": 50.0, "profit": -10.0, "written_off": 160.0}
    assert sales == {**sales, "count": 1, "revenue": 80.0, "cogs": 20.0, "profit": 60.0}
    t = rep["totals"]
    assert t["profit"] == 85.0 and t["revenue"] == 220.0 and t["tax"] == 10.0 and t["written_off"] == 160.0
    # drill-in: the jobs and sales behind each number
    kinds = {(x["kind"], x["id"]) for x in rep["rows"]}
    assert {k for k, _ in kinds} == {"job", "dropped", "sale"} and len(kinds) == 3
    assert ("job", w["a"]) in kinds and ("dropped", w["b"]) in kinds
    d = next(x for x in rep["rows"] if x["kind"] == "dropped")
    assert d["reason"] == "Too expensive" and d["written_off"] == 160.0
    assert sum(x["profit"] for x in rep["rows"]) == pytest.approx(t["profit"])


async def test_total_profit_reconciles_with_job_profitability(client, tenant, db):
    await _world(client, tenant, db)
    tp = (await client.get("/api/reports/total-profit", headers=tenant)).json()
    jp = (await client.get("/api/reports/job-profitability", headers=tenant)).json()
    jobs = tp["buckets"]["jobs"]
    # job profitability lists the same successful jobs, with the same numbers
    assert jp["totals"]["jobs"] == jobs["count"]
    assert jp["totals"]["revenue"] == jobs["revenue"]
    assert jp["totals"]["parts_cost"] == jobs["cogs"]
    assert jp["totals"]["labour_cost"] == jobs["labour_cost"]
    assert jp["totals"]["commission"] == jobs["commission"]
    assert jp["totals"]["margin"] == jobs["profit"]
    job_rows = {x["id"]: x["profit"] for x in tp["rows"] if x["kind"] == "job"}
    assert {r["order_id"]: r["margin"] for r in jp["rows"]} == job_rows
    # …and leaves the dropped job out of job profit
    assert all(r["order_id"] in job_rows for r in jp["rows"])


async def test_trend_against_the_previous_period(client, tenant, db):
    from backend.models import Order
    w = await _world(client, tenant, db)
    # move job A's close 20 days back: inside the previous 14-day window
    o = (await db.execute(select(Order).where(Order.id == w["a"]))).scalar_one()
    o.closed_at = datetime.utcnow() - timedelta(days=20)
    await db.commit()
    today = datetime.utcnow().date()
    q = {"date_from": (today - timedelta(days=13)).isoformat(), "date_to": today.isoformat()}
    rep = (await client.get("/api/reports/total-profit", params=q, headers=tenant)).json()
    assert rep["totals"]["profit"] == 50.0                 # −10 dropped + 60 sale
    assert rep["previous"]["profit"] == 35.0               # job A
    assert rep["previous"]["range"]["to"] < rep["range"]["from"]
    assert rep["change"]["profit"] == 15.0
    assert rep["change"]["profit_pct"] == pytest.approx(42.9, abs=0.1)


async def test_filters_by_location_and_technician(client, tenant, db):
    w = await _world(client, tenant, db)
    loc2 = (await client.post("/api/staff/locations", json={"name": "Branch 2"}, headers=tenant)).json()["id"]
    c = await _job(client, tenant, price=50, cost=10, loc=loc2)
    assert (await client.patch(f"/api/orders/{c}/status", json={"status": "done"}, headers=tenant)).status_code == 200
    rep = (await client.get("/api/reports/total-profit", params={"location_id": loc2}, headers=tenant)).json()
    assert {x["id"] for x in rep["rows"]} == {c}
    assert rep["totals"]["profit"] == 40.0
    by_loc = {x["location_id"]: x["profit"] for x in (await client.get(
        "/api/reports/total-profit", headers=tenant)).json()["by_location"]}
    assert by_loc[loc2] == 40.0 and by_loc[w["loc"]] == 85.0
    # technician: the job's assignee, the sale's seller
    tech = (await client.post("/api/staff/employees", json={
        "name": "Tina Tech", "email": f"t-{uuid.uuid4().hex[:6]}@t.dev", "password": "pw-test-12345"},
        headers=tenant)).json()["id"]
    d = await _job(client, tenant, price=70, cost=20, assignee=tech)
    assert (await client.patch(f"/api/orders/{d}/status", json={"status": "done"}, headers=tenant)).status_code == 200
    rep = (await client.get("/api/reports/total-profit", params={"technician_id": tech}, headers=tenant)).json()
    assert {x["id"] for x in rep["rows"]} == {d}
    by_tech = {x["employee_id"]: x["profit"] for x in (await client.get(
        "/api/reports/total-profit", headers=tenant)).json()["by_technician"]}
    assert by_tech[tech] == 50.0


async def _staff(client, H, perms):
    role = (await client.post("/api/staff/roles", json={"name": f"R{uuid.uuid4().hex[:4]}", "permissions": perms},
                              headers=H)).json()
    em = f"s-{uuid.uuid4().hex[:6]}@t.dev"
    r = await client.post("/api/staff/employees", json={"name": "Staff", "email": em, "password": "pw-test-12345",
                                                        "role_id": role["id"]}, headers=H)
    assert r.status_code == 201, r.text
    tok = (await client.post("/api/auth/token", data={"username": em, "password": "pw-test-12345"})).json()
    return {"Authorization": f"Bearer {tok['access_token']}"}


async def test_profit_needs_the_profit_permission(client, tenant, db):
    await _world(client, tenant, db)
    no = await _staff(client, tenant, {"can_view_reports": True, "can_see_profit": False})
    assert (await client.get("/api/reports/total-profit", headers=no)).status_code == 403
    assert (await client.get("/api/reports/run/dropped_jobs", headers=no)).status_code == 403


async def test_costs_hidden_without_purchase_price_permission(client, tenant, db):
    await _world(client, tenant, db)
    h = await _staff(client, tenant, {"can_view_reports": True, "can_see_profit": True,
                                      "can_view_purchase_price": False})
    r = await client.get("/api/reports/total-profit", headers=h)
    assert r.status_code == 200, r.text
    body = r.text
    for key in ('"profit"', '"cogs"', '"labour_cost"', '"commission"', '"profit_pct"', '"margin_pct"'):
        assert key not in body, key
    assert r.json()["totals"]["revenue"] == 220.0          # takings still show
    jp = await client.get("/api/reports/job-profitability", headers=h)
    if jp.status_code == 200:                               # gated by the cash-flow right on top
        for key in ('"parts_cost"', '"labour_cost"', '"commission"', '"margin"'):
            assert key not in jp.text, key
    assert (await client.get("/api/reports/run/wo_profit", headers=h)).status_code == 403
    # the dropped-jobs table carries costs in plain columns, so it needs both rights
    assert (await client.get("/api/reports/run/dropped_jobs", headers=h)).status_code == 403


async def test_other_shops_are_never_counted(client, tenant, db):
    await _world(client, tenant, db)
    email = f"o-{uuid.uuid4().hex[:6]}@t.dev"
    r = await client.post("/api/auth/register", json={"org_name": "Other", "name": "O", "email": email,
                                                      "password": "pw-test-12345"})
    H2 = {"Authorization": f"Bearer {r.json()['access_token']}"}
    rep = (await client.get("/api/reports/total-profit", headers=H2)).json()
    assert rep["rows"] == [] and rep["totals"]["profit"] == 0


async def test_unsuccessful_jobs_report(client, tenant, db):
    w = await _world(client, tenant, db)
    r = await client.get("/api/reports/run/dropped_jobs", headers=tenant)
    assert r.status_code == 200, r.text
    rep = r.json()
    cols = rep["columns"]
    row = dict(zip(cols, rep["rows"][0]))
    assert len(rep["rows"]) == 1
    assert row["Reason"] == "Too expensive"
    assert row["Money kept"] == 40.0 and row["Written off"] == 160.0
    assert row["Profit"] == -10.0
    # filter by status: a status with no dropped jobs → nothing
    r = await client.get("/api/reports/run/dropped_jobs", params={"status": "nonexistent"}, headers=tenant)
    assert r.json()["rows"] == []
    r = await client.get("/api/reports/run/dropped_jobs", params={"status": "dropped"}, headers=tenant)
    assert len(r.json()["rows"]) == 1
    assert w["b"]


async def test_period_is_capped_and_bad_dates_are_refused(client, tenant):
    r = await client.get("/api/reports/total-profit", params={"date_from": "2020-01-01", "date_to": "2026-01-01"},
                         headers=tenant)
    assert r.status_code == 422
    r = await client.get("/api/reports/total-profit", params={"date_from": "yesterday"}, headers=tenant)
    assert r.status_code == 422


async def test_date_only_range_ending_today_matches_job_profitability(client, tenant, db):
    await _world(client, tenant, db)
    today = datetime.utcnow().date().isoformat()
    start = (datetime.utcnow().date() - timedelta(days=7)).isoformat()
    tp = (await client.get("/api/reports/total-profit", params={"date_from": start, "date_to": today},
                           headers=tenant)).json()
    jp = (await client.get("/api/reports/job-profitability", params={"from_": start, "to": today},
                           headers=tenant)).json()
    assert tp["buckets"]["jobs"]["count"] == jp["totals"]["jobs"] == 1
    assert tp["buckets"]["jobs"]["profit"] == jp["totals"]["margin"]
