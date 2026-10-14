"""Build 144br — appointment availability is evaluated in the LOCATION's timezone,
not UTC. Fixes lead-time / window / past-slot drift for non-UTC shops."""
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from backend.routers.public import _local_now

WK = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def test_local_now_uses_zone():
    for tz in ["UTC", "Asia/Kolkata", "America/New_York", "Pacific/Kiritimati"]:
        got = _local_now(tz)
        exp = datetime.now(ZoneInfo(tz)).replace(tzinfo=None)
        assert got.tzinfo is None
        assert abs((got - exp).total_seconds()) < 5, tz


def test_local_now_bad_zone_falls_back_to_utc():
    got = _local_now("Not/AZone")
    exp = datetime.now(timezone.utc).replace(tzinfo=None)
    assert abs((got - exp).total_seconds()) < 5


def test_local_now_offset_is_real():
    # Kolkata is UTC+5:30 and never has DST → a fixed, checkable offset
    kol = _local_now("Asia/Kolkata")
    utc = _local_now("UTC")
    diff_min = round((kol - utc).total_seconds() / 60)
    assert diff_min == 330, diff_min


# ── integration: the engine still works end-to-end under a non-UTC tz ──
async def _tenant(client):
    email = f"admin-{uuid.uuid4().hex[:8]}@test.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Br Co", "name": "Owner", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_slots_under_nonutc_location(client):
    H = await _tenant(client)
    slug = (await client.get("/api/staff/public-pages", headers=H)).json()["slug"]
    d = (await client.get("/api/staff/locations", headers=H)).json()
    loc = (d if isinstance(d, list) else d["items"])[0]["id"]
    wh = {k: {"open": "10:00", "close": "12:00"} for k in WK}
    await client.put(f"/api/staff/locations/{loc}",
                     json={"name": "Main", "timezone": "Asia/Kolkata", "working_hours": wh}, headers=H)
    await client.put("/api/staff/public-pages", json={"appointment_time_slot": 60}, headers=H)

    day = (datetime.utcnow().date() + timedelta(days=7)).isoformat()
    r = await client.get(f"/api/public/appointment/{slug}/slots", params={"date": day})
    assert r.status_code == 200, r.text
    assert r.json()["slots"] == ["10:00", "11:00"], r.json()
