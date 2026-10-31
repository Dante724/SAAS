"""API v2 booking-site API (routers/v2_bookings.py): free slots from the public booking page's own
engine, appointment list/get/create, reschedule and cancel, with every v2 protection."""
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select, update

from backend.models import (
    AuditLog,
    Employee,
    Location,
    Organisation,
    Service,
    WebhookDelivery,
    WebhookEndpoint,
)
from backend.services.booking_slots import local_now

pytestmark = pytest.mark.asyncio

TZ = "America/New_York"
HOURS = {d: {"open": "09:00", "close": "17:00"} for d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")}


# ── helpers ──────────────────────────────────────────────────────────────────
async def _shop(client, db, **org_fields):
    """A shop with one location (09:00–17:00 every day, New York time), 60-minute slots, no lead
    time, and one appointment per slot unless overridden. Returns a dict of handles."""
    email = f"bk-{uuid.uuid4().hex[:8]}@t.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Book Co", "name": "Sam Kerr", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    H = {"Authorization": f"Bearer {r.json()['access_token']}"}
    me = (await client.get("/api/auth/me", headers=H)).json()
    org_id = me["org_id"]
    loc = (await db.execute(select(Location).where(Location.org_id == org_id))).scalars().first()
    await db.execute(update(Location).where(Location.id == loc.id).values(working_hours=HOURS, timezone=TZ))
    fields = {"appointment_time_slot": 60, "appointment_lead_min": 0, "appointment_window_days": 30,
              "appointment_allow_multiple": False, **org_fields}
    await db.execute(update(Organisation).where(Organisation.id == org_id).values(**fields))
    ola = Employee(org_id=org_id, name="Ola Nowak", email=f"ola-{uuid.uuid4().hex[:8]}@t.dev", password_hash="x")
    db.add(ola)
    await db.commit()
    W = await _key(client, H, ["write"])
    svc = (await client.post("/api/v2/services", json={"name": "Screen repair", "duration_min": 60},
                             headers=W)).json()["data"]
    return {"H": H, "W": W, "org": org_id, "loc": loc.id, "sam": me["id"], "ola": ola.id, "svc": svc["id"]}


async def _key(client, H, scopes):
    r = await client.post("/api/api-keys", json={"name": "k", "scopes": scopes}, headers=H)
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['key']}"}


def _day(n=2) -> str:
    return (local_now(TZ).date() + timedelta(days=n)).isoformat()


async def _slots(client, A, **qs):
    qs.setdefault("date", _day())
    r = await client.get("/api/v2/appointments/slots", params=qs, headers=A)
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _times(d) -> list[str]:
    return [s["time"] for s in d["slots"]]


async def _book(client, A, at="10:00", day=None, expect=201, headers=None, **body):
    body = {"scheduled_for": f"{day or _day()}T{at}:00", "client_name": "Dana Whitfield", **body}
    r = await client.post("/api/v2/appointments", json=body, headers={**A, **(headers or {})})
    assert r.status_code == expect, r.text
    return r.json().get("data") if expect < 300 else r.json()


async def _events(db, org_id, name):
    return (await db.execute(select(WebhookDelivery).where(
        WebhookDelivery.org_id == org_id, WebhookDelivery.event == name))).scalars().all()


# ── slots ────────────────────────────────────────────────────────────────────
async def test_slots_match_the_public_booking_page(client, db):
    s = await _shop(client, db)
    await _book(client, s["W"], "11:00", employee_id=s["sam"], service_id=s["svc"])
    for qs in ({}, {"service_id": s["svc"]}, {"employee_id": s["ola"]}, {"location_id": s["loc"]},
               {"employee_id": s["sam"], "service_id": s["svc"], "location_id": s["loc"]}):
        mine = await _slots(client, s["W"], **qs)
        pub = (await client.get(f"/api/public/appointment/{s['org']}/slots",
                                params={"date": _day(), **qs})).json()
        assert _times(mine) == pub["slots"], qs
        assert mine["location_id"] == pub["location_id"] == s["loc"]
        assert mine["step_min"] == pub["step"] and mine["timezone"] == TZ
    d = await _slots(client, s["W"])
    assert "11:00" not in _times(d) and _times(d)[0] == "09:00"
    first = d["slots"][0]
    assert first["start"] == f"{_day()}T09:00:00" and first["end"] == f"{_day()}T10:00:00"
    assert {e["id"] for e in first["employees"]} == {s["sam"], s["ola"]}
    assert (await _slots(client, s["W"], employee_id=s["sam"]))["slots"][0]["employees"] is None


async def test_employees_per_slot_follow_bookings_and_service_rules(client, db):
    s = await _shop(client, db, appointment_allow_multiple=True)
    await _book(client, s["W"], "10:00", employee_id=s["sam"])
    by = {x["time"]: {e["id"] for e in x["employees"]} for x in (await _slots(client, s["W"]))["slots"]}
    assert by["10:00"] == {s["ola"]} and by["09:00"] == {s["sam"], s["ola"]}
    await db.execute(update(Service).where(Service.id == s["svc"]).values(allowed_employee_ids=[s["sam"]]))
    await db.commit()
    by = {x["time"]: {e["id"] for e in x["employees"]}
          for x in (await _slots(client, s["W"], service_id=s["svc"]))["slots"]}
    assert by["09:00"] == {s["sam"]} and by["10:00"] == set()
    r = await client.get("/api/v2/appointments/slots", params={"date": _day(), "service_id": s["svc"],
                                                               "employee_id": s["ola"]}, headers=s["W"])
    assert r.status_code == 422 and "can't perform" in r.json()["detail"]


async def test_slots_respect_working_hours_and_closed_days(client, db):
    s = await _shop(client, db)
    day = local_now(TZ).date() + timedelta(days=2)
    key = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")[day.weekday()]
    await db.execute(update(Location).where(Location.id == s["loc"]).values(
        working_hours={**HOURS, key: {"open": "10:00", "close": "14:00"}}))
    await db.commit()
    assert _times(await _slots(client, s["W"])) == ["10:00", "11:00", "12:00", "13:00"]
    assert _times(await _slots(client, s["W"], duration_min=90)) == ["10:00", "11:00", "12:00"]
    await db.execute(update(Location).where(Location.id == s["loc"]).values(
        working_hours={**HOURS, key: {"closed": True}}))
    await db.commit()
    d = await _slots(client, s["W"])
    assert d["slots"] == [] and d["reason"] == "closed"


async def test_slots_respect_lead_time(client, db):
    s = await _shop(client, db)
    tomorrow = _day(1)
    assert _times(await _slots(client, s["W"], date=tomorrow))[0] == "09:00"
    await db.execute(update(Organisation).where(Organisation.id == s["org"]).values(appointment_lead_min=36 * 60))
    await db.commit()
    earliest = local_now(TZ) + timedelta(hours=36)
    got = await _slots(client, s["W"], date=tomorrow)
    assert all(datetime.fromisoformat(x["start"]) >= earliest - timedelta(minutes=1) for x in got["slots"])
    assert not {"09:00", "10:00", "11:00"} & set(_times(got))
    pub = (await client.get(f"/api/public/appointment/{s['org']}/slots", params={"date": tomorrow})).json()
    assert _times(got) == pub["slots"]
    # and booking inside the lead time is refused like any taken slot
    await _book(client, s["W"], "09:00", day=tomorrow, expect=409)


async def test_slots_validation(client, db):
    s = await _shop(client, db)
    A = s["W"]
    today = local_now(TZ).date()
    for qs, code in (({"date": (today - timedelta(days=1)).isoformat()}, 422),
                     ({"date": (today + timedelta(days=31)).isoformat()}, 422),
                     ({"date": "2026-13-45"}, 422), ({"date": "tomorrow"}, 422), ({}, 422),
                     ({"date": _day(), "duration_min": 2}, 422),
                     ({"date": _day(), "service_id": str(uuid.uuid4())}, 404),
                     ({"date": _day(), "employee_id": str(uuid.uuid4())}, 404),
                     ({"date": _day(), "location_id": str(uuid.uuid4())}, 404)):
        r = await client.get("/api/v2/appointments/slots", params=qs, headers=A)
        assert r.status_code == code, (qs, r.text)
    r = await client.get("/api/v2/appointments/slots", params={"date": (today + timedelta(days=31)).isoformat()},
                         headers=A)
    assert "30-day booking window" in r.json()["detail"]
    assert (await client.get("/api/v2/appointments/slots", params={"date": today.isoformat()},
                             headers=A)).status_code == 200
    await client.patch(f"/api/v2/services/{s['svc']}", json={"bookable": False}, headers=A)
    r = await client.get("/api/v2/appointments/slots", params={"date": _day(), "service_id": s["svc"]}, headers=A)
    assert r.status_code == 422 and "not marked bookable" in r.json()["detail"]
    await _book(client, A, service_id=s["svc"], expect=422)


# ── create ───────────────────────────────────────────────────────────────────
async def test_booked_slot_disappears_and_taken_slot_is_409(client, db):
    s = await _shop(client, db)
    b = await _book(client, s["W"], "10:00", employee_id=s["sam"], service_id=s["svc"], client_phone="+1 555 0100")
    assert b["status"] == "booked" and b["scheduled_for"] == f"{_day()}T10:00:00"
    assert b["service_name"] == "Screen repair" and b["employee_name"] == "Sam Kerr"
    assert b["location_id"] == s["loc"] and b["location_name"] and b["client_phone"] == "+1 555 0100"
    assert "10:00" not in _times(await _slots(client, s["W"]))
    pub = (await client.get(f"/api/public/appointment/{s['org']}/slots", params={"date": _day()})).json()
    assert "10:00" not in pub["slots"]
    r = await _book(client, s["W"], "10:00", employee_id=s["ola"], expect=409)
    assert "free slot" in r["detail"]
    # without the slot check the employee clash still applies, as in v1
    r = await _book(client, s["W"], "10:30", employee_id=s["sam"], check_slot=False, expect=409)
    assert "already has an appointment 10:00-11:00" in r["detail"]
    for bad in ("09:15", "16:30", "18:00"):
        await _book(client, s["W"], bad, expect=409)
    # an employee not allowed for the service
    await db.execute(update(Service).where(Service.id == s["svc"]).values(allowed_employee_ids=[s["sam"]]))
    await db.commit()
    r = await _book(client, s["W"], "13:00", employee_id=s["ola"], service_id=s["svc"], expect=422)
    assert "can't perform" in r["detail"]
    await _book(client, s["W"], "13:00", expect=422, client_name=None)   # who is it for?


async def test_wall_clock_offset_is_stored_as_location_time(client, db):
    s = await _shop(client, db)
    local = datetime.fromisoformat(f"{_day()}T10:00:00").replace(tzinfo=ZoneInfo(TZ))
    utc = local.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")
    r = await client.post("/api/v2/appointments", json={"scheduled_for": utc, "client_name": "Z"}, headers=s["W"])
    assert r.status_code == 201, r.text
    assert r.json()["data"]["scheduled_for"] == f"{_day()}T10:00:00"
    plus = local.astimezone(ZoneInfo("Asia/Kolkata")).isoformat()     # "+05:30"
    b = await _book(client, s["W"], employee_id=s["sam"], check_slot=False, day=None, at="00:00",
                    scheduled_for=plus, expect=201)
    assert b["scheduled_for"] == f"{_day()}T10:00:00"
    # rescheduling reads offsets the same way
    later = local.replace(hour=15).astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")
    r = await client.patch(f"/api/v2/appointments/{b['id']}", json={"scheduled_for": later}, headers=s["W"])
    assert r.status_code == 200 and r.json()["data"]["scheduled_for"] == f"{_day()}T15:00:00"


async def test_idempotency_replay(client, db):
    s = await _shop(client, db)
    k = {"Idempotency-Key": "book-" + uuid.uuid4().hex}
    body = {"scheduled_for": f"{_day()}T10:00:00", "client_name": "Dana", "employee_id": s["sam"]}
    r1 = await client.post("/api/v2/appointments", json=body, headers={**s["W"], **k})
    r2 = await client.post("/api/v2/appointments", json=body, headers={**s["W"], **k})
    assert r1.status_code == r2.status_code == 201
    assert r1.json() == r2.json() and r2.headers.get("Idempotent-Replayed") == "true"
    lst = (await client.get("/api/v2/appointments", headers=s["W"])).json()
    assert lst["total"] == 1
    r3 = await client.post("/api/v2/appointments", json={**body, "client_name": "Other"}, headers={**s["W"], **k})
    assert r3.status_code == 422
    # cancel takes a key too, and a replay returns the stored response
    ck = {"Idempotency-Key": "cancel-" + uuid.uuid4().hex}
    url = f"/api/v2/appointments/{r1.json()['data']['id']}/cancel"
    c1 = await client.post(url, json={"reason": "Ill"}, headers={**s["W"], **ck})
    c2 = await client.post(url, json={"reason": "Ill"}, headers={**s["W"], **ck})
    assert c1.status_code == c2.status_code == 200 and c2.headers.get("Idempotent-Replayed") == "true"


# ── reschedule ───────────────────────────────────────────────────────────────
async def test_reschedule_rules(client, db):
    s = await _shop(client, db, appointment_allow_multiple=True)
    a = await _book(client, s["W"], "10:00", employee_id=s["sam"])
    b = await _book(client, s["W"], "12:00", employee_id=s["sam"])
    url = f"/api/v2/appointments/{b['id']}"
    r = await client.patch(url, json={"scheduled_for": f"{_day()}T10:30:00"}, headers=s["W"])
    assert r.status_code == 409 and "clashes with an appointment 10:00-11:00" in r.json()["detail"]
    r = await client.patch(url, json={"duration_min": 150, "scheduled_for": f"{_day()}T08:00:00"}, headers=s["W"])
    assert r.status_code == 409
    # moving to a free employee at the same time is fine; changing only the note too
    r = await client.patch(url, json={"scheduled_for": f"{_day()}T10:30:00", "employee_id": s["ola"]}, headers=s["W"])
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["employee_id"] == s["ola"] and d["employee_name"] == "Ola Nowak" and d["ends_at"] == f"{_day()}T11:30:00"
    r = await client.patch(url, json={"note": "Bring charger"}, headers=s["W"])
    assert r.status_code == 200 and r.json()["data"]["note"] == "Bring charger"
    assert (await client.patch(url, json={}, headers=s["W"])).status_code == 422
    assert (await client.patch(url, json={"when": "now"}, headers=s["W"])).status_code == 422
    assert (await client.patch(url, json={"employee_id": str(uuid.uuid4())}, headers=s["W"])).status_code == 404
    # done (in the app) can't be rescheduled or cancelled
    r = await client.patch(f"/api/appointments/{a['id']}", json={"status": "done"}, headers=s["H"])
    assert r.status_code == 200, r.text
    r = await client.patch(f"/api/v2/appointments/{a['id']}", json={"scheduled_for": f"{_day()}T15:00:00"},
                           headers=s["W"])
    assert r.status_code == 409 and "this one is done" in r.json()["detail"]
    assert (await client.post(f"/api/v2/appointments/{a['id']}/cancel", headers=s["W"])).status_code == 409


async def test_reschedule_notifies_the_client_like_the_app(client, db):
    s = await _shop(client, db)
    cid = (await client.post("/api/clients", json={"name": "Jo", "email": "jo@x.dev"}, headers=s["H"])).json()["id"]
    for ev in ("changed", "status_changed"):
        r = await client.post("/api/notifications/rules", json={
            "module": "appointment", "event": ev, "channel": "email"}, headers=s["H"])
        assert r.status_code == 201, r.text
    b = await _book(client, s["W"], "10:00", client_id=cid, client_name=None)
    assert b["client_name"] == "Jo" and b["client_id"] == cid
    await client.patch(f"/api/v2/appointments/{b['id']}", json={"scheduled_for": f"{_day()}T14:00:00"},
                       headers=s["W"])
    await client.post(f"/api/v2/appointments/{b['id']}/cancel", headers=s["W"])
    logs = (await client.get("/api/notifications/log", headers=s["H"])).json()["items"]
    got = {x["trigger_event"] for x in logs if x["recipient"] == "jo@x.dev"}
    assert {"changed", "status_changed"} <= got, logs


# ── cancel ───────────────────────────────────────────────────────────────────
async def test_cancel_frees_the_slot_and_is_idempotent(client, db):
    s = await _shop(client, db)
    b = await _book(client, s["W"], "10:00", employee_id=s["sam"])
    assert "10:00" not in _times(await _slots(client, s["W"]))
    url = f"/api/v2/appointments/{b['id']}/cancel"
    r1 = await client.post(url, json={"reason": "Customer called"}, headers=s["W"])
    assert r1.status_code == 200, r1.text
    d = r1.json()["data"]
    assert d["status"] == "canceled" and "Cancelled: Customer called" in d["note"]
    assert "10:00" in _times(await _slots(client, s["W"]))
    r2 = await client.post(url, headers=s["W"])
    assert r2.status_code == 200 and r2.json()["data"] == d
    assert (await client.post(url, json={"why": "x"}, headers=s["W"])).status_code == 422
    # the freed slot can be booked again, by the same employee
    await _book(client, s["W"], "10:00", employee_id=s["sam"])


async def test_events_fire_once(client, db):
    s = await _shop(client, db)
    db.add(WebhookEndpoint(org_id=s["org"], url="https://hooks.example.com/in", events="*", active=True))
    await db.commit()
    b = await _book(client, s["W"], "10:00", employee_id=s["sam"])
    url = f"/api/v2/appointments/{b['id']}"
    await client.patch(url, json={"scheduled_for": f"{_day()}T13:00:00", "note": "moved"}, headers=s["W"])
    await client.patch(url, json={"scheduled_for": f"{_day()}T13:00:00"}, headers=s["W"])  # no change, no event
    await client.post(f"{url}/cancel", json={"reason": "Ill"}, headers=s["W"])
    await client.post(f"{url}/cancel", headers=s["W"])
    import json
    created = await _events(db, s["org"], "appointment.created")
    updated = await _events(db, s["org"], "appointment.updated")
    cancelled = await _events(db, s["org"], "appointment.cancelled")
    assert (len(created), len(updated), len(cancelled)) == (1, 1, 1)
    assert json.loads(created[0].payload)["data"]["scheduled_for"] == f"{_day()}T10:00:00"
    up = json.loads(updated[0].payload)["data"]
    assert sorted(up["changed_fields"]) == ["note", "scheduled_for"] and up["scheduled_for"] == f"{_day()}T13:00:00"
    cx = json.loads(cancelled[0].payload)["data"]
    assert cx["status"] == "canceled" and cx["reason"] == "Ill" and cx["id"] == b["id"]
    # every write is audited against the key
    rows = (await db.execute(select(AuditLog).where(AuditLog.org_id == s["org"],
                                                    AuditLog.entity_id == b["id"]))).scalars().all()
    assert {r.action for r in rows} >= {"appointment.create", "appointment.update", "appointment.cancel"}
    assert all(r.diff.get("via_api_key") for r in rows if r.action.startswith("appointment."))


# ── list / get ───────────────────────────────────────────────────────────────
async def test_list_filters_and_get(client, db):
    s = await _shop(client, db, appointment_allow_multiple=True)
    a = await _book(client, s["W"], "10:00", employee_id=s["sam"], service_id=s["svc"])
    b = await _book(client, s["W"], "11:00", day=_day(3), employee_id=s["ola"])
    c = await _book(client, s["W"], "12:00", day=_day(4))
    await client.post(f"/api/v2/appointments/{c['id']}/cancel", headers=s["W"])

    async def ids(**qs):
        r = await client.get("/api/v2/appointments", params=qs, headers=s["W"])
        assert r.status_code == 200, r.text
        return [x["id"] for x in r.json()["data"]]
    assert await ids() == [a["id"], b["id"], c["id"]]                      # soonest first
    assert await ids(date_from=_day(3)) == [b["id"], c["id"]]
    assert await ids(date_to=_day(3)) == [a["id"], b["id"]]
    assert await ids(date_from=_day(3), date_to=_day(3)) == [b["id"]]
    assert await ids(employee_id=s["ola"]) == [b["id"]]
    assert await ids(location_id=s["loc"]) == [a["id"], b["id"], c["id"]]
    assert await ids(status="canceled") == [c["id"]]
    assert await ids(updated_since=(datetime.utcnow() + timedelta(minutes=5)).isoformat() + "Z") == []
    assert len(await ids(updated_since=(datetime.utcnow() - timedelta(minutes=5)).isoformat() + "Z")) == 3
    assert await ids(page_size=1, page=2) == [b["id"]]
    for bad in ({"status": "lost"}, {"date_from": "28/10/2026"}, {"page_size": 101}, {"page": 0}):
        assert (await client.get("/api/v2/appointments", params=bad, headers=s["W"])).status_code == 422, bad
    g = (await client.get(f"/api/v2/appointments/{a['id']}", headers=s["W"])).json()["data"]
    assert g["service_name"] == "Screen repair" and g["employee_name"] == "Sam Kerr" and g["status"] == "booked"
    assert g["location_id"] == s["loc"] and g["updated_at"]


# ── scopes and tenancy ───────────────────────────────────────────────────────
async def test_scopes(client, db):
    s = await _shop(client, db)
    b = await _book(client, s["W"], "10:00")
    RO = await _key(client, s["H"], ["appointments:read"])
    RW = await _key(client, s["H"], ["appointments:write"])
    OTHER = await _key(client, s["H"], ["inquiries:write"])
    url = f"/api/v2/appointments/{b['id']}"
    for A in (RO, RW):
        assert (await client.get("/api/v2/appointments", headers=A)).status_code == 200
        assert (await client.get(url, headers=A)).status_code == 200
        assert (await client.get("/api/v2/appointments/slots", params={"date": _day()}, headers=A)).status_code == 200
    assert (await client.post("/api/v2/appointments", json={"scheduled_for": f"{_day()}T12:00:00",
                                                           "client_name": "x"}, headers=RO)).status_code == 403
    assert (await client.patch(url, json={"note": "x"}, headers=RO)).status_code == 403
    assert (await client.post(f"{url}/cancel", headers=RO)).status_code == 403
    for path in ("/api/v2/appointments", url):
        assert (await client.get(path, headers=OTHER)).status_code == 403
    assert (await client.get("/api/v1/appointments", headers=RO)).status_code == 403     # v1 needs broad scopes
    await _book(client, RW, "12:00")
    # contacts stay behind contacts:read
    assert (await client.get(url, headers=RO)).json()["data"]["client_name"] == "Dana Whitfield"
    cid = (await client.post("/api/clients", json={"name": "Jo"}, headers=s["H"])).json()["id"]
    assert (await client.get("/api/v2/appointments", params={"client_id": cid}, headers=RO)).status_code == 403
    assert (await _book(client, RW, "13:00", client_id=cid, expect=403))
    c = await _book(client, s["W"], "14:00", client_id=cid)
    assert (await client.get(f"/api/v2/appointments/{c['id']}", headers=RO)).json()["data"]["client_id"] is None
    assert (await client.get("/api/v2/appointments", params={"client_id": cid}, headers=s["W"])).json()["total"] == 1


async def test_other_shops_ids_are_404(client, db):
    mine = await _shop(client, db)
    theirs = await _shop(client, db)
    b = await _book(client, mine["W"], "10:00", employee_id=mine["sam"])
    T = theirs["W"]
    url = f"/api/v2/appointments/{b['id']}"
    assert (await client.get(url, headers=T)).status_code == 404
    assert (await client.patch(url, json={"note": "x"}, headers=T)).status_code == 404
    assert (await client.post(f"{url}/cancel", headers=T)).status_code == 404
    for field in ("service_id", "employee_id", "location_id"):
        ref = {"service_id": mine["svc"], "employee_id": mine["sam"], "location_id": mine["loc"]}[field]
        r = await client.get("/api/v2/appointments/slots", params={"date": _day(), field: ref}, headers=T)
        assert r.status_code == 404, field
        await _book(client, T, "11:00", expect=404, **{field: ref})
        if field != "service_id":
            r = await client.get("/api/v2/appointments", params={field: ref}, headers=T)
            assert r.status_code == 404, field
    # and their appointment is still there, untouched
    assert (await client.get(url, headers=mine["W"])).json()["data"]["status"] == "booked"


async def test_openapi_documents_the_booking_routes(client):
    doc = (await client.get("/api/v2/openapi.json")).json()
    for path, method in (("/api/v2/appointments/slots", "get"), ("/api/v2/appointments", "get"),
                         ("/api/v2/appointments", "post"), ("/api/v2/appointments/{aid}", "get"),
                         ("/api/v2/appointments/{aid}", "patch"), ("/api/v2/appointments/{aid}/cancel", "post")):
        op = doc["paths"][path][method]
        assert op["summary"] and op["description"], (path, method)
    assert "Idempotency-Key" in {p["name"] for p in doc["paths"]["/api/v2/appointments"]["post"]["parameters"]}
