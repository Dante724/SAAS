"""Build 144m — Payroll accruals list (enriched /runs) + mark-paid."""
import pytest


async def _emp(client, h, name, email, wage=1000):
    return (await client.post("/api/staff/employees", json={
        "name": name, "email": email, "password": "pw-emp-12345",
        "wage_type": "fixed", "wage_value": wage}, headers=h)).json()["id"]


@pytest.mark.asyncio
async def test_runs_list_is_enriched_and_can_be_paid(client, tenant):
    eid = await _emp(client, tenant, "Acc Ann", "ann-acr@test.dev", 1000)
    await client.post("/api/payroll/adjustment", json={
        "employee_id": eid, "kind": "bonus", "amount": 100, "month": "2026-06"}, headers=tenant)
    await client.post("/api/payroll/adjustment", json={
        "employee_id": eid, "kind": "penalty", "amount": 40, "month": "2026-06"}, headers=tenant)

    acc = await client.post("/api/payroll/calculation/accrue", json={
        "employee_id": eid, "month": "2026-06"}, headers=tenant)
    assert acc.status_code == 201, acc.text
    run_id = acc.json()["id"]

    listing = (await client.get("/api/payroll/runs", headers=tenant)).json()
    assert "totals" in listing
    row = next(r for r in listing["items"] if r["id"] == run_id)
    # enriched fields present
    assert row["employee_name"] == "Acc Ann"
    assert row["base_salary"] == 1000
    assert row["bonuses_total"] == 100
    assert row["penalties_total"] == 40
    assert row["gross_total"] == 1060          # 1000 + 100 - 40
    assert row["status"] == "accrued"
    assert row["accrued_at"] is not None
    assert row["paid_at"] is None

    # mark paid
    pay = await client.post(f"/api/payroll/runs/{run_id}/pay", json={}, headers=tenant)
    assert pay.status_code == 200, pay.text
    assert pay.json()["status"] == "paid"

    row2 = next(r for r in (await client.get("/api/payroll/runs", headers=tenant)).json()["items"]
                if r["id"] == run_id)
    assert row2["status"] == "paid"
    assert row2["paid_at"] is not None


@pytest.mark.asyncio
async def test_cannot_mark_missing_run_paid(client, tenant):
    r = await client.post("/api/payroll/runs/does-not-exist/pay", json={}, headers=tenant)
    assert r.status_code == 404
