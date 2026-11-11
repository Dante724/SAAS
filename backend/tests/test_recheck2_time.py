"""Launch recheck 2 (B3, B6): times that still came out in the wrong clock.

Each test goes through the real API."""
import uuid

import pytest
from sqlalchemy import update

from backend.core.utc_instants import mark_utc
from backend.models import Organisation

pytestmark = pytest.mark.asyncio


async def _signup(client):
    r = await client.post("/api/auth/register", json={
        "org_name": "Fixit", "name": "Owner", "email": f"t-{uuid.uuid4().hex[:8]}@t.dev",
        "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}, r.json()["org_id"]


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


# ── B6: instants carry a "Z", wall-clock times don't ──────────────────────────────────────────
async def test_shift_history_times_are_utc(client, tenant):
    """Recheck: the drawer's shift history sent opened_at bare, and the till showed UTC as local."""
    loc = await _loc(client, tenant)
    r = await client.post("/api/finance/shifts/open", json={"location_id": loc, "opening_float": 50},
                          headers=tenant)
    assert r.status_code == 200, r.text
    assert r.json()["opened_at"].endswith("Z")
    rows = (await client.get(f"/api/finance/shifts?location_id={loc}", headers=tenant)).json()["items"]
    assert rows and all(s["opened_at"].endswith("Z") for s in rows)


async def test_order_thread_times_are_utc_and_due_times_stay_wall_clock(client, tenant):
    """Recheck: the order thread's "at" was bare. due_at and scheduled_for are the shop's clock and
    must not gain a zone."""
    r = await client.post("/api/orders", json={
        "complaint": "Screen", "due_at": "2026-12-01T17:00:00",
        "scheduled_for": "2026-12-01T10:30:00"}, headers=tenant)
    assert r.status_code == 201, r.text
    oid = r.json()["id"]
    assert (await client.post(f"/api/orders/{oid}/thread", json={"body": "Ready soon"},
                              headers=tenant)).status_code == 201
    thread = (await client.get(f"/api/orders/{oid}/thread", headers=tenant)).json()
    rows = thread["items"] if isinstance(thread, dict) else thread
    assert rows and all(m["at"].endswith("Z") for m in rows)
    o = (await client.get(f"/api/orders/{oid}", headers=tenant)).json()
    assert o["created_at"].endswith("Z")
    assert o["due_at"].startswith("2026-12-01T17:00") and not o["due_at"].endswith("Z")
    assert o["scheduled_for"].startswith("2026-12-01T10:30") and not o["scheduled_for"].endswith("Z")


async def test_mark_utc_rules():
    out = mark_utc({"created_at": "2026-11-11T07:14:43.506311", "at": "2026-11-11T07:14",
                    "opened_at": "2026-11-11T07:14:43Z", "paid_at": "2026-11-11T07:14:43+05:30",
                    "issued_at": "2026-11-11", "due_at": "2026-11-11T17:00:00",
                    "scheduled_for": "2026-11-11T10:00:00", "note_at": None, "name_at": "x",
                    "rows": [{"predicted_ready_at": "2026-11-11T09:00:00"}]})
    assert out == {"created_at": "2026-11-11T07:14:43.506311Z", "at": "2026-11-11T07:14Z",
                   "opened_at": "2026-11-11T07:14:43Z", "paid_at": "2026-11-11T07:14:43+05:30",
                   "issued_at": "2026-11-11", "due_at": "2026-11-11T17:00:00",
                   "scheduled_for": "2026-11-11T10:00:00", "note_at": None, "name_at": "x",
                   "rows": [{"predicted_ready_at": "2026-11-11T09:00:00Z"}]}


# ── B3: "In today" is the shop's day ──────────────────────────────────────────────────────────
async def test_cash_in_today_uses_the_shop_day(client, db):
    """Recheck: a New York shop's 21:00 sale (02:00 UTC next day) fell out of that evening's
    "In today" and into the next day's."""
    h, org_id = await _signup(client)
    await db.execute(update(Organisation).where(Organisation.id == org_id)
                     .values(timezone="America/New_York"))
    await db.commit()
    r = await client.post("/api/cash/accounts", json={"name": "Till", "payment_option": "cash"}, headers=h)
    assert r.status_code == 201, r.text
    acc = r.json()["id"]
    # 10 Nov 21:00 in New York = 11 Nov 02:00 UTC; actual_date is stored as naive UTC.
    r = await client.post("/api/cash/transactions", json={
        "account_id": acc, "txn_type": "income", "amount": 80, "actual_date": "2026-11-11T02:00:00"},
        headers=h)
    assert r.status_code == 201, r.text

    async def in_on(day):
        r = await client.get(f"/api/cash/transactions?account_id={acc}&summary=true&day={day}", headers=h)
        assert r.status_code == 200, r.text
        return r.json()["summary"]["today_in"]

    assert await in_on("2026-11-10") == pytest.approx(80)
    assert await in_on("2026-11-11") == 0


# ── Review follow-ups ─────────────────────────────────────────────────────────────────────────
async def test_backdated_payment_is_that_day_on_the_shop_clock(client, db):
    """A payment backdated to a day was kept as UTC midnight; sent with its new "Z" a New York
    screen showed it on the day before. It is now noon of that day on the shop's clock."""
    from datetime import date, datetime, timedelta
    from zoneinfo import ZoneInfo

    from backend.tests.test_opb_billing import _account, _client, _inv, _invoice
    h, org_id = await _signup(client)
    await db.execute(update(Organisation).where(Organisation.id == org_id)
                     .values(timezone="America/New_York"))
    await db.commit()
    cid, acc = await _client(client, h), await _account(client, h)
    iid = await _invoice(client, h, cid)
    past = date.today() - timedelta(days=3)
    r = await client.post(f"/api/finance/invoices/{iid}/pay", headers=h,
                          json={"amount": 10, "method": "cash", "account_id": acc, "paid_at": past.isoformat()})
    assert r.status_code == 200, r.text
    paid = (await _inv(client, h, iid))["payments"][0]["paid_at"]
    assert paid.endswith("Z")
    local = datetime.fromisoformat(paid).astimezone(ZoneInfo("America/New_York"))
    assert local.date() == past and local.hour == 12


async def test_v1_writes_send_instants_with_z(client, db):
    """v1/v2 writes return through their own response (idempotency, replay); they mark instants
    like reads do, so a created order's created_at matches what GET returns."""
    from backend.tests.test_api_v1_hardening import _auth, _key, _location
    from backend.tests.test_api_v1_hardening import _register as _v1_register
    H, org, _ = await _v1_register(client)
    A = _auth(await _key(client, H))
    loc = await _location(db, org)
    for headers in (A, {**A, "Idempotency-Key": "z-1"}, {**A, "Idempotency-Key": "z-1"}):
        r = await client.post("/api/v1/orders", json={"location_id": loc}, headers=headers)
        assert r.status_code == 201, r.text
        assert r.json()["data"]["created_at"].endswith("Z"), r.json()
