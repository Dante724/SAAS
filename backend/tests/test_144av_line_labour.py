"""144av — per-line labour attribution + booked-vs-actual + fractional split.

#1 assignee is already PATCHable (see test_engage_multitech); here we cover:
#2 booked_hours round-trips on create/GET/PATCH, and /labor returns per-line actual seconds.
#3 a service split across two techs (co_technicians) pays each their share of the commission.
"""
import pytest
from sqlalchemy import select

from backend.services.payroll import _line_splits


async def _loc(client, tenant):
    return (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]


async def _order(client, tenant, **extra):
    body = {"location_id": await _loc(client, tenant), "complaint": "x"}
    body.update(extra)
    r = await client.post("/api/orders", json=body, headers=tenant)
    assert r.status_code in (200, 201), r.text
    return r.json()


# ── #3 unit: split resolution ────────────────────────────────
def test_line_splits_prefers_co_technicians():
    class L:  # duck-typed line
        co_technicians = [{"employee_id": "a", "share": 0.5}, {"employee_id": "b", "share": 0.5}]
        technician_id = "a"; work_share = 1.0
    assert _line_splits(L()) == [("a", 0.5), ("b", 0.5)]


def test_line_splits_falls_back_to_single_then_assignee():
    class L1:
        co_technicians = []; technician_id = "solo"; work_share = 1.0
    assert _line_splits(L1()) == [("solo", 1.0)]

    class L2:
        co_technicians = []; technician_id = None; work_share = 1.0
    assert _line_splits(L2(), fallback="mgr") == [("mgr", 1.0)]
    assert _line_splits(L2()) == []   # nobody → no pay


# ── #2: booked_hours round-trips + /labor by_line ────────────
@pytest.mark.asyncio
async def test_booked_hours_roundtrip_and_actual_by_line(client, tenant):
    o = await _order(client, tenant)
    r = await client.post(f"/api/orders/{o['id']}/lines", json={
        "line_type": "labour", "kind": "service", "description": "Brake pads",
        "qty": 1, "unit_price": 100, "booked_hours": 1.5}, headers=tenant)
    assert r.status_code in (200, 201), r.text
    detail = (await client.get(f"/api/orders/{o['id']}", headers=tenant)).json()
    line = detail["lines"][-1]
    assert line["booked_hours"] == 1.5
    lid = line["id"]
    # edit booked hours via PATCH
    rp = await client.patch(f"/api/orders/{o['id']}/lines/{lid}", json={"booked_hours": 2.0}, headers=tenant)
    assert rp.status_code == 200, rp.text
    detail2 = (await client.get(f"/api/orders/{o['id']}", headers=tenant)).json()
    assert detail2["lines"][-1]["booked_hours"] == 2.0

    # clock a little time into the line → /labor shows it under by_line
    await client.post("/api/timeclock/start?switch=true", json={
        "order_id": o["id"], "order_line_id": lid, "category": "job"}, headers=tenant)
    lab = (await client.get(f"/api/orders/{o['id']}/labor", headers=tenant)).json()
    assert "by_line" in lab
    assert any(bl["order_line_id"] == lid for bl in lab["by_line"])


# ── #3 integration: split commission pays each tech their share ──
@pytest.mark.asyncio
async def test_split_line_pays_each_share(client, tenant, db):
    from backend.models import CommissionRule, Employee, EmployeeBalanceLedger
    from backend.services.auth import hash_password

    me = (await client.get("/api/auth/me", headers=tenant)).json()
    t2 = Employee(org_id=me["org_id"], name="Split Two", email="split2@x.dev",
                  password_hash=hash_password("pw-123456789"), active=True)
    db.add(t2)
    db.add(CommissionRule(org_id=me["org_id"], employee_id=None, name="Labor 10%",
                          event="labor_performed", calc_type="percent",
                          percent_basis="line_total", amount=10.0))
    await db.commit()
    await db.refresh(t2)

    term = await client.post("/api/statuses", json={
        "name": "Done AV", "key": "done-av", "status_group": "closed", "is_terminal": True}, headers=tenant)
    assert term.status_code == 201
    o = await _order(client, tenant)
    # one $200 service split 0.5/0.5 between me and t2
    r = await client.post(f"/api/orders/{o['id']}/lines", json={
        "line_type": "labour", "kind": "service", "description": "Board repair",
        "qty": 1, "unit_price": 200, "technician_id": me["id"],
        "co_technicians": [{"employee_id": me["id"], "share": 0.5},
                           {"employee_id": t2.id, "share": 0.5}]}, headers=tenant)
    assert r.status_code in (200, 201), r.text
    closed = await client.patch(f"/api/orders/{o['id']}/status", json={"status": "done-av"}, headers=tenant)
    assert closed.status_code == 200, closed.text

    rows = (await db.execute(select(EmployeeBalanceLedger).where(
        EmployeeBalanceLedger.event == "labor_performed"))).scalars().all()
    by_emp = {x.employee_id: x.amount for x in rows}
    # 10% of $200 = $20, split 50/50 → $10 each
    assert by_emp.get(me["id"]) == pytest.approx(10.0)
    assert by_emp.get(t2.id) == pytest.approx(10.0)


# ── 144aw hardening: validation guards ───────────────────────
@pytest.mark.asyncio
async def test_split_shares_must_sum_to_one(client, tenant):
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    o = await _order(client, tenant)
    r = await client.post(f"/api/orders/{o['id']}/lines", json={
        "line_type": "labour", "kind": "service", "description": "Bad split",
        "qty": 1, "unit_price": 100, "technician_id": me["id"],
        "co_technicians": [{"employee_id": me["id"], "share": 0.8},
                           {"employee_id": me["id"], "share": 0.8}]}, headers=tenant)
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_foreign_technician_rejected(client, tenant):
    o = await _order(client, tenant)
    r = await client.post(f"/api/orders/{o['id']}/lines", json={
        "line_type": "labour", "kind": "service", "description": "Foreign tech",
        "qty": 1, "unit_price": 100, "technician_id": "not-a-real-employee"}, headers=tenant)
    assert r.status_code == 422, r.text


# ── 144ax: catalog-prefill, flat-rate billing, efficiency report ──────────────
@pytest.mark.asyncio
async def test_booked_hours_prefill_from_service_duration(client, tenant):
    """G1 — adding a catalog service with a 90-min duration snapshots booked_hours=1.5
    (minutes→hours) without the tech typing it."""
    svc = (await client.post("/api/catalog/services", json={
        "name": "Diag", "price": 80, "duration_min": 90}, headers=tenant)).json()
    o = await _order(client, tenant)
    await client.post(f"/api/orders/{o['id']}/lines", json={
        "line_type": "labour", "kind": "service", "ref_id": svc["id"],
        "description": "Diag", "qty": 1, "unit_price": 80}, headers=tenant)
    line = (await client.get(f"/api/orders/{o['id']}", headers=tenant)).json()["lines"][-1]
    assert line["booked_hours"] == 1.5


@pytest.mark.asyncio
async def test_flat_rate_service_bills_booked_hours(client, tenant):
    """G3 — a flat-rate service bills booked hours × rate: qty syncs to 2.0h, total = 2×$100."""
    svc = (await client.post("/api/catalog/services", json={
        "name": "Brake flat", "price": 100, "duration_min": 120, "flat_rate": True}, headers=tenant)).json()
    o = await _order(client, tenant)
    r = await client.post(f"/api/orders/{o['id']}/lines", json={
        "line_type": "labour", "kind": "service", "ref_id": svc["id"],
        "description": "Brake flat", "qty": 1, "unit_price": 100}, headers=tenant)
    assert r.status_code in (200, 201), r.text
    line = (await client.get(f"/api/orders/{o['id']}", headers=tenant)).json()["lines"][-1]
    assert line["qty"] == 2.0 and line["total"] == pytest.approx(200.0)


@pytest.mark.asyncio
async def test_non_flat_rate_totals_unchanged(client, tenant):
    """G3 golden-master — a normal (non-flat-rate) service is byte-identical to before:
    qty stays 1, total = qty×price."""
    svc = (await client.post("/api/catalog/services", json={
        "name": "Normal", "price": 100, "duration_min": 120}, headers=tenant)).json()
    o = await _order(client, tenant)
    await client.post(f"/api/orders/{o['id']}/lines", json={
        "line_type": "labour", "kind": "service", "ref_id": svc["id"],
        "description": "Normal", "qty": 1, "unit_price": 100}, headers=tenant)
    line = (await client.get(f"/api/orders/{o['id']}", headers=tenant)).json()["lines"][-1]
    assert line["qty"] == 1.0 and line["total"] == pytest.approx(100.0)
    assert line["booked_hours"] == 2.0   # still snapshots duration, just doesn't bill by it


@pytest.mark.asyncio
async def test_efficiency_report_matches_inline_actual(client, tenant):
    """G2 — the Labour-efficiency report's actual hours for a tech equals the inline per-line
    actual (same source of truth), and booked comes through."""
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    svc = (await client.post("/api/catalog/services", json={
        "name": "Eff svc", "price": 50, "duration_min": 60}, headers=tenant)).json()
    o = await _order(client, tenant)
    await client.post(f"/api/orders/{o['id']}/lines", json={
        "line_type": "labour", "kind": "service", "ref_id": svc["id"],
        "description": "Eff svc", "qty": 1, "unit_price": 50, "technician_id": me["id"]}, headers=tenant)
    lid = (await client.get(f"/api/orders/{o['id']}", headers=tenant)).json()["lines"][-1]["id"]
    await client.post("/api/timeclock/start?switch=true", json={
        "order_id": o["id"], "order_line_id": lid, "category": "job"}, headers=tenant)
    lab = (await client.get(f"/api/orders/{o['id']}/labor", headers=tenant)).json()
    inline_actual_h = sum(b["total_seconds"] for b in lab["by_line"]) / 3600
    rep = (await client.get("/api/reports/run/labor_efficiency", headers=tenant)).json()
    assert rep["columns"][:3] == ["Technician", "Booked h", "Actual h"]
    # one tech row; its actual matches the inline widget's sum (same definition)
    row = next(r for r in rep["rows"] if r[0] == me["name"])
    assert row[1] == pytest.approx(1.0)          # booked 1.0h from duration
    assert row[2] == pytest.approx(inline_actual_h, abs=0.02)
