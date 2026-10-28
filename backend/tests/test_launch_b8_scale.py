"""Launch blocker B8: one shop must not be able to hurt every other shop on the server.

* Home (company dashboard + "needs you" feed) used to load the shop's whole order and parts
  history into Python; ten concurrent opens on a 30k-order shop took both workers to 4.8 GB.
* The technician's My Work built an IN list of every open order in the shop and hit SQLite's
  "too many SQL variables" at 33,208 orders.

The datasets here are small, but the checks are the ones that scale: no statement binds a
parameter per row, and the request never loads the shop's orders/parts as ORM objects."""
from contextlib import contextmanager
from datetime import datetime, timedelta

import pytest
from sqlalchemy import event, insert, select

N = 300            # rows per shop: enough that a per-row IN list or load is obvious
MAX_PARAMS = 50    # a fixed-shape query never needs more than this


@contextmanager
def _watch(db, *models):
    """Record every statement's bound-parameter count and how many ORM rows of `models` loaded.
    `db` is the test session: the app's requests run on the same engine."""
    engine = db.bind.sync_engine
    stats = {"max_params": 0, "loaded": 0, "statements": 0}

    def _before(conn, cursor, statement, parameters, context, executemany):
        n = len(parameters) if isinstance(parameters, (list, tuple, dict)) else 0
        stats["max_params"] = max(stats["max_params"], n)
        stats["statements"] += 1

    def _load(target, ctx):
        stats["loaded"] += 1

    event.listen(engine, "before_cursor_execute", _before)
    for m in models:
        event.listen(m, "load", _load)
    try:
        yield stats
    finally:
        event.remove(engine, "before_cursor_execute", _before)
        for m in models:
            event.remove(m, "load", _load)


async def _me(client, h):
    return (await client.get("/api/auth/me", headers=h)).json()


async def _bulk_orders(db, org_id, loc_id, n, **kw):
    """Insert n work orders in one statement (the API would take minutes for thousands)."""
    import uuid

    from backend.models import Order
    now = datetime.utcnow()
    rows = [dict(id=str(uuid.uuid4()), org_id=org_id, location_id=loc_id, order_number=f"B8-{i}",
                 status=kw.get("status", "new"), priority=1 if i % 10 == 0 else 0,
                 total=10.0, created_at=now - timedelta(days=i % 60),
                 due_at=now - timedelta(days=1) if i % 7 == 0 else None,
                 assigned_to_id=kw.get("assigned_to_id"), is_estimate=False, archived=False)
            for i in range(n)]
    await db.execute(insert(Order), rows)
    await db.commit()
    return [r["id"] for r in rows]


@pytest.mark.asyncio
async def test_company_dashboard_is_sql_aggregates(client, tenant, db):
    from backend.models import Order
    me = await _me(client, tenant)
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    await _bulk_orders(db, me["org_id"], loc, N, assigned_to_id=me["id"])

    with _watch(db, Order) as s:
        r = await client.get("/api/reports/company-dashboard", headers=tenant)
        r_loc = await client.get(f"/api/reports/company-dashboard?location_id={loc}", headers=tenant)
    assert r.status_code == 200, r.text
    assert r_loc.status_code == 200, r_loc.text
    assert s["loaded"] == 0, f"Home loaded {s['loaded']} work orders into memory"
    assert s["max_params"] <= MAX_PARAMS, f"a Home query bound {s['max_params']} parameters"

    body = r.json()
    assert body["kpis"]["new"] == N
    assert body["kpis"]["urgent"] == len([i for i in range(N) if i % 10 == 0])
    assert body["kpis"]["overdue"] == len([i for i in range(N) if i % 7 == 0])
    assert body["kpis"]["receivables"] == N * 10.0
    assert body["by_assignee"][0]["name"] == "Admin" and body["by_assignee"][0]["total"] == N
    assert sum(m["count"] for m in body["status_mix"]) == N
    assert r_loc.json()["kpis"]["new"] == N


@pytest.mark.asyncio
async def test_home_feed_low_stock_has_no_per_part_in_list(client, tenant, db):
    import uuid

    from backend.models import Part
    me = await _me(client, tenant)
    await db.execute(insert(Part), [dict(id=str(uuid.uuid4()), org_id=me["org_id"], name=f"P{i}",
                                         min_stock=1, active=True) for i in range(N)])
    await db.commit()
    with _watch(db, Part) as s:
        r = await client.get("/api/reports/home-feed", headers=tenant)
    assert r.status_code == 200, r.text
    assert s["loaded"] == 0, f"the feed loaded {s['loaded']} parts into memory"
    assert s["max_params"] <= MAX_PARAMS, f"a feed query bound {s['max_params']} parameters"
    low = [i for i in r.json()["items"] if i["key"] == "lowstock"]
    assert low and low[0]["count"] == N


async def _tech(db, org_id, loc_id, email):
    from backend.models import Employee
    from backend.services.auth import hash_password
    t = Employee(org_id=org_id, location_id=loc_id, name="Tech", email=email,
                 password_hash=hash_password("pw-tech-12345"), active=True)
    db.add(t)
    await db.commit()
    return t


@pytest.mark.asyncio
async def test_my_work_is_scoped_to_the_techs_own_jobs(client, tenant, db):
    """33,208 orders used to 500 with "too many SQL variables": My Work put every open order
    in the shop into an IN list. Now it only reads the tech's own lines."""
    from backend.models import Order, OrderLine
    me = await _me(client, tenant)
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    await _bulk_orders(db, me["org_id"], loc, N)                     # the rest of the shop
    mine = (await client.post("/api/orders", json={"location_id": loc, "complaint": "screen"},
                              headers=tenant)).json()["id"]
    db.add(OrderLine(order_id=mine, kind="service", line_type="labour", description="Swap screen",
                     qty=1, unit_price=50, technician_id=me["id"]))
    await db.commit()

    with _watch(db, Order) as s:
        r = await client.get("/api/timeclock/my-work", headers=tenant)
    assert r.status_code == 200, r.text
    assert s["max_params"] <= MAX_PARAMS, f"My Work bound {s['max_params']} parameters"
    assert s["loaded"] <= 1, f"My Work loaded {s['loaded']} work orders; only the tech's own is needed"
    body = r.json()
    assert [l["description"] for l in body["lines"]] == ["Swap screen"]
    assert [o["order_id"] for o in body["orders"]] == [mine]


@pytest.mark.asyncio
async def test_tech_dashboard_does_not_load_every_finished_job(client, tenant, db):
    """The Go app's Today screen: a tech with years of closed jobs used to load all of them."""
    from backend.models import Order
    me = await _me(client, tenant)
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    ids = await _bulk_orders(db, me["org_id"], loc, N, assigned_to_id=me["id"])
    closed = ids[: N - 5]
    from sqlalchemy import update
    await db.execute(update(Order).where(Order.id.in_(closed)).values(closed_at=datetime.utcnow()))
    await db.commit()

    with _watch(db, Order) as s:
        r = await client.get("/api/tech/dashboard", headers=tenant)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["stats"]["completed"] == N - 5 and body["stats"]["assigned_total"] == N
    assert len(body["completed"]) == 25
    assert s["loaded"] <= 5 + 25, f"Today loaded {s['loaded']} work orders"
    assert s["max_params"] <= MAX_PARAMS


# ── notification sweep ──────────────────────────────────────────────────────────

async def _org_with_due_message(client, db, email):
    from backend.models import NotificationLog
    r = await client.post("/api/auth/register", json={
        "org_name": email, "name": "Owner", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    oid = (await _me(client, h))["org_id"]
    db.add(NotificationLog(org_id=oid, channel="email", recipient="c@x.dev", subject="Ready",
                           body="Your device is ready", status="scheduled",
                           send_at=datetime.utcnow() - timedelta(minutes=1)))
    await db.commit()
    return oid


@pytest.mark.asyncio
async def test_one_shops_db_error_does_not_stall_the_sweep(client, db, monkeypatch):
    """512 orgs, a DB error in one, 510 failed: the error left the shared session needing a
    rollback, so every org after it failed too. Each org now gets its own session."""
    from backend.core import scheduled
    from backend.models import NotificationLog
    from backend.services import notification_rules as nr
    from sqlalchemy.ext.asyncio import async_sessionmaker

    orgs = [await _org_with_due_message(client, db, f"shop{i}@b8.dev") for i in range(4)]
    seen = []

    real_queue = nr.queue_reminders

    async def _queue(session, *, org_id, now=None):
        seen.append(org_id)
        if len(seen) == 1:    # the first shop swept hits a DB error; a failed flush poisons the session
            session.add(NotificationLog(org_id=org_id, channel=None, body="x"))
            await session.flush()
        return await real_queue(session, org_id=org_id, now=now)

    async def _send(*a, **k):
        return {"status": "sent"}

    monkeypatch.setattr(nr, "queue_reminders", _queue)
    monkeypatch.setattr(nr, "_send", _send)
    monkeypatch.setattr("backend.db.database.AsyncSessionLocal",
                        async_sessionmaker(db.bind, expire_on_commit=False))
    await scheduled.sweep_notifications()

    db.expire_all()
    good = [o for o in orgs if o != seen[0]]
    rows = (await db.execute(select(NotificationLog.org_id, NotificationLog.status)
                             .where(NotificationLog.org_id.in_(good)))).all()
    assert sorted(s for _, s in rows) == ["sent"] * 3, rows


async def _shop_now(db, org_id):
    """Job and appointment times are the shop's wall clock, not UTC."""
    from backend.services.dates import local_now, shop_tzname
    return local_now(await shop_tzname(db, org_id, None))


# ── SMS retry dedup ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_failed_reminder_is_not_requeued_every_tick(client, tenant, db, monkeypatch):
    """A Twilio timeout marked the reminder "failed"; the next sweep (every 2 minutes) saw no
    scheduled/sent row and queued it again — about 30 texts an hour to one customer."""
    from backend.models import Client, NotificationLog, NotificationRule, Order
    from backend.services import notification_rules as nr
    me = await _me(client, tenant)
    oid = me["org_id"]
    c = Client(org_id=oid, name="Ann", phone="+15550001")
    db.add(c)
    await db.flush()
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    o = Order(org_id=oid, location_id=loc, order_number="R-1", client_id=c.id, status="new",
              scheduled_for=await _shop_now(db, oid) + timedelta(minutes=30))
    db.add(o)
    db.add(NotificationRule(org_id=oid, module="order", event="reminder", channel="sms",
                            reminder_offset_min=60, active=True))
    await db.commit()

    calls = []

    async def _send(*a, **k):
        calls.append(k.get("recipient"))
        return {"status": "failed", "error": "Twilio timed out"}

    monkeypatch.setattr(nr, "_send", _send)
    for _ in range(5):                     # five sweep ticks
        await nr.queue_reminders(db, org_id=oid)
        await nr.run_due(db, org_id=oid)
    rows = (await db.execute(select(NotificationLog).where(NotificationLog.order_id == o.id))).scalars().all()
    assert len(rows) == 1, [r.status for r in rows]
    assert len(calls) == 1, f"the customer was texted {len(calls)} times"


@pytest.mark.asyncio
async def test_failed_appointment_reminder_is_not_requeued(client, tenant, db, monkeypatch):
    from backend.models import Appointment, Client, NotificationLog, NotificationRule
    from backend.services import notification_rules as nr
    oid = (await _me(client, tenant))["org_id"]
    c = Client(org_id=oid, name="Bo", phone="+15550002")
    db.add(c)
    await db.flush()
    db.add(Appointment(org_id=oid, client_id=c.id, status="booked",
                       scheduled_for=await _shop_now(db, oid) + timedelta(minutes=30)))
    db.add(NotificationRule(org_id=oid, module="appointment", event="reminder", channel="sms",
                            reminder_offset_min=60, active=True))
    await db.commit()

    calls = []

    async def _send(*a, **k):
        calls.append(1)
        return {"status": "failed", "error": "Twilio timed out"}

    monkeypatch.setattr(nr, "_send", _send)
    for _ in range(5):
        await nr.queue_appointment_reminders(db, org_id=oid)
        await nr.run_due(db, org_id=oid)
    n = len((await db.execute(select(NotificationLog).where(NotificationLog.client_id == c.id))).scalars().all())
    assert n == 1 and len(calls) == 1
