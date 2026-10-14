"""Builds 73-75 — work schedules & timesheet variance, scheduler free-slot search,
work-order transfer between locations, and the estimate-approval timestamp."""
import pytest


async def _loc(client, H):
    return (await client.get("/api/staff/locations", headers=H)).json()["items"][0]["id"]


async def _order(client, H, **extra):
    body = {"location_id": await _loc(client, H), "complaint": "x"}
    body.update(extra)
    r = await client.post("/api/orders", json=body, headers=H)
    assert r.status_code in (200, 201), r.text
    return r.json()


# ── Build 73: work schedules ──────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_create_and_list_schedule(client, tenant):
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    r = await client.post("/api/schedules", json={
        "employee_id": me["id"], "day": "2026-08-03", "kind": "shift",
        "start": "09:00", "end": "17:00"}, headers=tenant)
    assert r.status_code == 201, r.text
    assert r.json()["planned_hours"] == 8.0
    lst = (await client.get(
        f"/api/schedules?date_from=2026-08-01&date_to=2026-08-07&employee_id={me['id']}",
        headers=tenant)).json()
    assert len(lst["items"]) == 1 and lst["items"][0]["start"] == "09:00"


@pytest.mark.asyncio
async def test_shift_end_must_follow_start(client, tenant):
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    r = await client.post("/api/schedules", json={
        "employee_id": me["id"], "day": "2026-08-04", "kind": "shift",
        "start": "17:00", "end": "09:00"}, headers=tenant)
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_absence_and_timesheet_variance(client, tenant):
    """Planned vs actual: an 8h planned shift with no clocked time = −8h variance."""
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    await client.post("/api/schedules", json={
        "employee_id": me["id"], "day": "2026-08-05", "kind": "shift",
        "start": "09:00", "end": "17:00"}, headers=tenant)
    await client.post("/api/schedules", json={
        "employee_id": me["id"], "day": "2026-08-06", "kind": "vacation"}, headers=tenant)
    cmp_ = (await client.get(
        f"/api/schedules/compare?employee_id={me['id']}&date_from=2026-08-01&date_to=2026-08-31",
        headers=tenant)).json()
    assert cmp_["planned_hours"] == 8.0        # vacation contributes no planned hours
    assert cmp_["actual_hours"] == 0.0
    assert cmp_["variance_hours"] == -8.0
    assert any(a["kind"] == "vacation" for a in cmp_["absences"])


# ── Build 74: free-slot search ────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_free_slots_respect_shift_and_booked_orders(client, tenant):
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    await client.post("/api/schedules", json={
        "employee_id": me["id"], "day": "2026-08-10", "kind": "shift",
        "start": "09:00", "end": "17:00"}, headers=tenant)
    # book 10:00–12:00
    o = await _order(client, tenant, assigned_to_id=me["id"])
    r = await client.patch(f"/api/orders/{o['id']}/schedule", json={
        "scheduled_for": "2026-08-10T10:00", "scheduled_to": "2026-08-10T12:00"}, headers=tenant)
    assert r.status_code == 200, r.text

    slots = (await client.get(
        f"/api/orders/scheduler/free-slots?date_str=2026-08-10&duration_min=60&employee_id={me['id']}",
        headers=tenant)).json()["slots"]
    windows = {(s["start"], s["end"]) for s in slots}
    assert ("09:00", "10:00") in windows      # before the appointment
    assert ("12:00", "17:00") in windows      # after it
    # a 2h job can't fit in the 1h morning gap
    long_slots = (await client.get(
        f"/api/orders/scheduler/free-slots?date_str=2026-08-10&duration_min=120&employee_id={me['id']}",
        headers=tenant)).json()["slots"]
    assert ("09:00", "10:00") not in {(s["start"], s["end"]) for s in long_slots}


@pytest.mark.asyncio
async def test_free_slots_empty_on_day_off(client, tenant):
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    await client.post("/api/schedules", json={
        "employee_id": me["id"], "day": "2026-08-11", "kind": "day_off"}, headers=tenant)
    r = (await client.get(
        f"/api/orders/scheduler/free-slots?date_str=2026-08-11&employee_id={me['id']}",
        headers=tenant)).json()
    assert r["slots"] == []


# ── Build 75: transfer + estimate approval ────────────────────────────────────
@pytest.mark.asyncio
async def test_transfer_order_between_locations(client, tenant, db):
    from backend.models import Location
    o = await _order(client, tenant)
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    loc2 = Location(org_id=me["org_id"], name="Branch 2")
    db.add(loc2)
    await db.commit()
    await db.refresh(loc2)

    r = await client.post(f"/api/orders/{o['id']}/transfer",
                          json={"location_id": loc2.id}, headers=tenant)
    assert r.status_code == 200, r.text
    assert r.json()["location_id"] == loc2.id

    from sqlalchemy import select

    from backend.models import Order
    row = (await db.execute(select(Order).where(Order.id == o["id"]))).scalar_one()
    await db.refresh(row)
    assert row.location_id == loc2.id


@pytest.mark.asyncio
async def test_transfer_rejects_foreign_location(client, tenant):
    o = await _order(client, tenant)
    r = await client.post(f"/api/orders/{o['id']}/transfer",
                          json={"location_id": "does-not-exist"}, headers=tenant)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_estimate_approval_stamps_timestamp(client, tenant):
    """Customer accepting on the public track page records when they approved."""
    est = await _order(client, tenant, is_estimate=True)
    tok = (await client.post(f"/api/orders/{est['id']}/public-link", json={}, headers=tenant)).json()["token"]
    r = await client.post(f"/api/public/track/{tok}/respond", json={"decision": "accept"})
    assert r.status_code == 200, r.text
    detail = (await client.get(f"/api/orders/{est['id']}", headers=tenant)).json()
    assert detail["estimate_approved_at"] is not None
