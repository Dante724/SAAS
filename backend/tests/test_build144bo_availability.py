"""Build 144bo — appointment availability engine: a public slot is offered only
when it sits inside the location's working hours for that weekday, honours the
lead time and appointment window, and respects single- vs. double-appointment capacity."""
import uuid
from datetime import datetime, timedelta

from backend.services.dates import now_utc

WK = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


async def _tenant(client):
    email = f"admin-{uuid.uuid4().hex[:8]}@test.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Bo Co", "name": "Owner", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _slug(client, H):
    return (await client.get("/api/staff/public-pages", headers=H)).json()["slug"]


async def _loc(client, H):
    d = (await client.get("/api/staff/locations", headers=H)).json()
    return (d if isinstance(d, list) else d["items"])[0]["id"]


def _future_date(weekday_delta=7):
    """A date `weekday_delta` days from now (deterministic, inside a 30d window)."""
    return (now_utc().date() + timedelta(days=weekday_delta))


async def test_slots_follow_location_working_hours(client):
    H = await _tenant(client)
    slug = await _slug(client, H)
    loc = await _loc(client, H)
    day = _future_date(7)
    # open every weekday 10:00-12:00 so a 60-min slot yields exactly 10:00 & 11:00
    wh = {k: {"open": "10:00", "close": "12:00"} for k in WK}
    await client.put(f"/api/staff/locations/{loc}", json={"name": "Main", "working_hours": wh}, headers=H)
    await client.put("/api/staff/public-pages", json={"appointment_time_slot": 60}, headers=H)

    r = await client.get(f"/api/public/appointment/{slug}/slots", params={"date": day.isoformat()})
    assert r.status_code == 200, r.text
    assert r.json()["slots"] == ["10:00", "11:00"], r.json()


async def test_closed_weekday_yields_no_slots(client):
    H = await _tenant(client)
    slug = await _slug(client, H)
    loc = await _loc(client, H)
    day = _future_date(8)
    wh = {k: {"open": "09:00", "close": "17:00"} for k in WK}
    wh[WK[day.weekday()]] = {"closed": True}          # shut on that weekday
    await client.put(f"/api/staff/locations/{loc}", json={"name": "Main", "working_hours": wh}, headers=H)

    r = await client.get(f"/api/public/appointment/{slug}/slots", params={"date": day.isoformat()})
    assert r.status_code == 200
    assert r.json()["slots"] == []
    assert r.json()["reason"] == "closed"


async def test_beyond_window_is_rejected(client):
    H = await _tenant(client)
    slug = await _slug(client, H)
    await client.put("/api/staff/public-pages", json={"appointment_window_days": 7}, headers=H)
    far = (now_utc().date() + timedelta(days=40)).isoformat()
    r = await client.get(f"/api/public/appointment/{slug}/slots", params={"date": far})
    assert r.status_code == 200
    assert r.json()["slots"] == []
    assert "window" in r.json()["reason"]


async def test_days_endpoint_flags_open_days(client):
    H = await _tenant(client)
    slug = await _slug(client, H)
    loc = await _loc(client, H)
    wh = {k: {"open": "09:00", "close": "17:00"} for k in WK}
    # close Sundays
    wh["sun"] = {"closed": True}
    await client.put(f"/api/staff/locations/{loc}", json={"name": "Main", "working_hours": wh}, headers=H)

    start = _future_date(1).isoformat()
    r = await client.get(f"/api/public/appointment/{slug}/days",
                         params={"start": start, "days": 14})
    assert r.status_code == 200, r.text
    days = r.json()["days"]
    assert len(days) == 14
    for d in days:
        wkd = datetime.fromisoformat(d["date"]).weekday()
        if wkd == 6:                     # Sunday closed
            assert d["open"] is False, d
        else:
            assert d["open"] is True and d["free"] > 0, d


async def test_single_appointment_capacity_blocks_double(client):
    H = await _tenant(client)
    slug = await _slug(client, H)
    loc = await _loc(client, H)
    day = _future_date(9)
    wh = {k: {"open": "10:00", "close": "12:00"} for k in WK}
    await client.put(f"/api/staff/locations/{loc}", json={"name": "Main", "working_hours": wh}, headers=H)
    # turn OFF double-appointment → location holds one appointment per window
    await client.put("/api/staff/public-pages",
                     json={"appointment_time_slot": 60, "appointment_allow_multiple": False}, headers=H)

    b1 = await client.post(f"/api/public/appointment/{slug}", json={
        "name": "First", "phone": "111", "date": day.isoformat(), "time": "10:00"})
    assert b1.status_code == 201, b1.text

    # that slot is now gone from availability
    slots = (await client.get(f"/api/public/appointment/{slug}/slots",
                              params={"date": day.isoformat()})).json()["slots"]
    assert "10:00" not in slots and "11:00" in slots, slots

    # a second appointment on the same window is refused
    b2 = await client.post(f"/api/public/appointment/{slug}", json={
        "name": "Second", "phone": "222", "date": day.isoformat(), "time": "10:00"})
    assert b2.status_code == 409, b2.text


async def test_appointment_outside_hours_rejected(client):
    H = await _tenant(client)
    slug = await _slug(client, H)
    loc = await _loc(client, H)
    day = _future_date(10)
    wh = {k: {"open": "10:00", "close": "12:00"} for k in WK}
    await client.put(f"/api/staff/locations/{loc}", json={"name": "Main", "working_hours": wh}, headers=H)
    r = await client.post(f"/api/public/appointment/{slug}", json={
        "name": "Late", "phone": "333", "date": day.isoformat(), "time": "18:00"})
    assert r.status_code == 409, r.text
