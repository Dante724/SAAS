"""Payroll calculation page — penalties/bonuses, detail, accrual (build 144h)."""
import pytest


async def _emp(client, h, name, email, wage_type="fixed", wage_value=0):
    r = await client.post("/api/staff/employees", json={
        "name": name, "email": email, "password": "pw-emp-12345",
        "wage_type": wage_type, "wage_value": wage_value}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.mark.asyncio
async def test_calculation_includes_position_and_totals(client, tenant):
    await _emp(client, tenant, "Sal", "sal-calc@test.dev", "fixed", 1500)
    r = await client.get("/api/payroll/calculation?month=2026-06", headers=tenant)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "totals" in body
    row = next(i for i in body["items"] if i["name"] == "Sal")
    for k in ("base_salary", "commissions_total", "penalties_total", "bonuses_total",
              "computed_total", "position"):
        assert k in row, k
    assert row["base_salary"] == 1500
    assert row["computed_total"] == 1500  # base only, nothing else yet


@pytest.mark.asyncio
async def test_penalty_and_bonus_flow_into_calculation(client, tenant):
    eid = await _emp(client, tenant, "Bri", "bri-calc@test.dev", "fixed", 0)

    b = await client.post("/api/payroll/adjustment", json={
        "employee_id": eid, "kind": "bonus", "amount": 200, "note": "Great month",
        "month": "2026-06"}, headers=tenant)
    assert b.status_code == 201, b.text
    p = await client.post("/api/payroll/adjustment", json={
        "employee_id": eid, "kind": "penalty", "amount": 50, "note": "Late",
        "month": "2026-06"}, headers=tenant)
    assert p.status_code == 201, p.text

    row = next(i for i in (await client.get(
        "/api/payroll/calculation?month=2026-06", headers=tenant)).json()["items"]
        if i["employee_id"] == eid)
    assert row["bonuses_total"] == 200
    assert row["penalties_total"] == 50
    assert row["computed_total"] == 150   # 0 base + 200 bonus - 50 penalty

    # a different month is unaffected
    row_jul = next(i for i in (await client.get(
        "/api/payroll/calculation?month=2026-07", headers=tenant)).json()["items"]
        if i["employee_id"] == eid)
    assert row_jul["bonuses_total"] == 0 and row_jul["penalties_total"] == 0

    # detail drawer lists them and validation rejects bad input
    detail = (await client.get(
        f"/api/payroll/calculation/detail?employee_id={eid}&month=2026-06", headers=tenant)).json()
    assert len(detail["bonuses"]) == 1 and len(detail["penalties"]) == 1
    assert detail["computed_total"] == 150

    bad = await client.post("/api/payroll/adjustment", json={
        "employee_id": eid, "kind": "bonus", "amount": -5}, headers=tenant)
    assert bad.status_code == 400

    # removing the bonus updates the calculation
    bonus_id = b.json()["id"]
    d = await client.delete(f"/api/payroll/adjustment/{bonus_id}", headers=tenant)
    assert d.status_code == 200
    row2 = next(i for i in (await client.get(
        "/api/payroll/calculation?month=2026-06", headers=tenant)).json()["items"]
        if i["employee_id"] == eid)
    assert row2["bonuses_total"] == 0
    assert row2["computed_total"] == -50


@pytest.mark.asyncio
async def test_accrual_snapshots_and_blocks_current_month(client, tenant):
    eid = await _emp(client, tenant, "Acc", "acc-calc@test.dev", "fixed", 1000)
    await client.post("/api/payroll/adjustment", json={
        "employee_id": eid, "kind": "bonus", "amount": 100, "month": "2026-06"}, headers=tenant)

    # a past month accrues and snapshots the totals
    acc = await client.post("/api/payroll/calculation/accrue", json={
        "employee_id": eid, "month": "2026-06"}, headers=tenant)
    assert acc.status_code == 201, acc.text
    body = acc.json()
    assert body["status"] == "accrued"
    assert body["base_salary"] == 1000 and body["bonuses_total"] == 100
    assert body["gross_total"] == 1100

    # second accrual for the same month is refused
    dupe = await client.post("/api/payroll/calculation/accrue", json={
        "employee_id": eid, "month": "2026-06"}, headers=tenant)
    assert dupe.status_code == 409

    # the current/future month cannot be accrued
    future = await client.post("/api/payroll/calculation/accrue", json={
        "employee_id": eid, "month": "2099-01"}, headers=tenant)
    assert future.status_code == 400
