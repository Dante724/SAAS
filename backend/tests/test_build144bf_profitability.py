"""Build 144bf — Job Profitability report.

Margin per closed WO = revenue − parts cost − labour cost (timer + manual),
rolled up per location, gated by can_view_cashflow.
"""
import uuid
from datetime import datetime, timedelta

import pytest


async def _tenant(client):
    email = f"admin-{uuid.uuid4().hex[:8]}@test.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Pf Co", "name": "Admin", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _loc(client, H):
    d = (await client.get("/api/staff/locations", headers=H)).json()
    return (d if isinstance(d, list) else d["items"])[0]["id"]


async def _order(client, H, loc):
    cid = (await client.post("/api/clients", json={"name": "Cust"}, headers=H)).json()["id"]
    return (await client.post("/api/orders", json={
        "client_id": cid, "location_id": loc, "complaint": "fix"}, headers=H)).json()


async def test_margin_includes_parts_and_manual_labour(client):
    H = await _tenant(client)
    loc = await _loc(client, H)
    # an employee with a labour cost rate of 60/hr
    emp = (await client.post("/api/staff/employees", json={
        "name": "Tech", "email": f"t-{uuid.uuid4().hex[:6]}@t.dev",
        "password": "pw-test-12345", "wage_type": "hourly", "wage_value": 60}, headers=H)).json()
    o = await _order(client, H, loc)
    # product line: revenue 100, cost 30
    await client.post(f"/api/orders/{o['id']}/lines", json={
        "line_type": "part", "kind": "product", "description": "Battery",
        "qty": 1, "unit_price": 100, "cost_price": 30}, headers=H)
    # manual labour: 60 min against this WO for that employee → labour cost 60
    now = datetime.utcnow()
    await client.post("/api/timeclock/entries", json={
        "employee_id": emp["id"], "order_id": o["id"], "category": "job",
        "started_at": (now - timedelta(hours=2)).isoformat(),
        "duration_min": 60}, headers=H)
    # close the WO so it enters the report
    await client.patch(f"/api/orders/{o['id']}/status", json={"status": "closed"}, headers=H)

    rep = (await client.get("/api/reports/job-profitability", headers=H)).json()
    row = [r for r in rep["rows"] if r["order_id"] == o["id"]][0]
    assert row["revenue"] == 100
    assert row["parts_cost"] == 30
    assert row["labour_cost"] == 60
    assert row["margin"] == 10          # 100 − 30 − 60
    assert row["has_labour"] is True
    assert rep["totals"]["margin"] == 10
    assert rep["by_location"][0]["margin"] == 10


async def test_no_labour_flag_and_open_orders_excluded(client):
    H = await _tenant(client)
    loc = await _loc(client, H)
    # closed WO, revenue 50, no time logged → has_labour False, labour 0
    o1 = await _order(client, H, loc)
    await client.post(f"/api/orders/{o1['id']}/lines", json={
        "line_type": "part", "kind": "product", "description": "Cable",
        "qty": 1, "unit_price": 50, "cost_price": 10}, headers=H)
    await client.patch(f"/api/orders/{o1['id']}/status", json={"status": "closed"}, headers=H)
    # an OPEN WO must not appear
    o2 = await _order(client, H, loc)
    await client.post(f"/api/orders/{o2['id']}/lines", json={
        "line_type": "part", "kind": "product", "description": "X",
        "qty": 1, "unit_price": 999, "cost_price": 1}, headers=H)

    rep = (await client.get("/api/reports/job-profitability", headers=H)).json()
    ids = [r["order_id"] for r in rep["rows"]]
    assert o1["id"] in ids and o2["id"] not in ids
    row = [r for r in rep["rows"] if r["order_id"] == o1["id"]][0]
    assert row["has_labour"] is False
    assert row["labour_cost"] == 0
    assert row["margin"] == 40          # 50 − 10 − 0


async def test_report_requires_finance_permission(client):
    H = await _tenant(client)
    role = (await client.post("/api/staff/roles", json={
        "name": "Floor", "permissions": {"can_view_cashflow": False}}, headers=H)).json()
    em = f"f-{uuid.uuid4().hex[:6]}@t.dev"
    await client.post("/api/staff/employees", json={
        "name": "Floor", "email": em, "password": "pw-test-12345", "role_id": role["id"]}, headers=H)
    tok = (await client.post("/api/auth/token", data={"username": em, "password": "pw-test-12345"})).json()
    FH = {"Authorization": f"Bearer {tok['access_token']}"}
    r = await client.get("/api/reports/job-profitability", headers=FH)
    assert r.status_code == 403
