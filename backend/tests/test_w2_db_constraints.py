"""Audit 2026-11-12, wave 2 schema rules (docs/audits/2026-11-12-backend-audit/02-schema-findings.md).

- DB-04: "only one open at a time" now has database backing: one open cash drawer per location,
  one running timer per person, one live membership per plan per asset. A request that slips past
  the app's own check (a race) gets a plain 409, never a 500.
- DB-05 / FIN-17: sale, transfer, write-off, purchase-return and posting numbers are unique per
  shop; numbering skips a number already issued, so a re-pointed counter can't reuse one.
- DB-06: coupon and gift-card codes are unique per shop (a duplicate made the code unusable).
- DB-07: CHECK constraints on money/stock signs and on fixed status/kind values.
- The migration (w2b01) repairs existing data first and logs what it changed.

Each test failed before its fix.
"""
import os
import sqlite3
import subprocess
import sys
import uuid
from datetime import date, datetime
from pathlib import Path

import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from backend.core.db_errors import unique_violation_message
from backend.models import (
    CashShift,
    Coupon,
    GiftCard,
    Invoice,
    Membership,
    MembershipPlan,
    Posting,
    PurchaseReturn,
    Sale,
    StockLevel,
    StockTransfer,
    TimeEntry,
    WriteOff,
)

pytestmark = pytest.mark.asyncio


# ── helpers ─────────────────────────────────────────────────────────────────────────────────────

async def _register(client):
    email = f"w2-{uuid.uuid4().hex[:8]}@t.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Wave Two", "name": "Owner", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    H = {"Authorization": f"Bearer {r.json()['access_token']}"}
    me = (await client.get("/api/auth/me", headers=H)).json()
    loc = (await client.get("/api/staff/locations", headers=H)).json()["items"][0]["id"]
    return H, me["org_id"], me["id"], loc


async def _refused(db, *rows):
    """Adding these rows is refused by the database; returns the error."""
    for r in rows:
        db.add(r)
    with pytest.raises(IntegrityError) as ei:
        await db.commit()
    await db.rollback()
    return ei.value


async def _client_and_asset(client, H):
    cid = (await client.post("/api/clients", json={"name": "Sam", "phone": "5550142233"}, headers=H)).json()["id"]
    r = await client.post("/api/assets", json={"owner_type": "client", "client_id": cid,
                                               "brand": "Bayliner", "model": "VR5"}, headers=H)
    assert r.status_code in (200, 201), r.text
    return cid, r.json()["id"]


async def _plan(client, H):
    r = await client.post("/api/memberships/plans", json={
        "name": "Boat Care", "price_monthly": 45, "price_yearly": 449,
        "items": [{"kind": "perk", "label": "Priority slot"}]}, headers=H)
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ── DB-04: one open at a time ───────────────────────────────────────────────────────────────────

async def test_a_second_open_drawer_at_a_location_is_refused_with_a_plain_message(client, db):
    H, org, me, loc = await _register(client)
    r = await client.post("/api/finance/shifts/open", json={"location_id": loc, "opening_float": 100}, headers=H)
    assert r.status_code == 200, r.text
    err = await _refused(db, CashShift(location_id=loc, opened_by_id=me, opening_float=0))
    assert unique_violation_message(err) == "A cash drawer is already open at this location — close it first."
    # a closed drawer next to the open one is fine (history)
    db.add(CashShift(location_id=loc, opened_by_id=me, opening_float=0, closed_at=datetime(2026, 1, 1)))
    await db.commit()


async def test_a_second_running_timer_is_refused_and_a_race_gets_409(client, db, monkeypatch):
    H, org, me, loc = await _register(client)
    r = await client.post("/api/timeclock/start", json={"category": "admin"}, headers=H)
    assert r.status_code == 201, r.text
    err = await _refused(db, TimeEntry(org_id=org, employee_id=me, category="admin", state="running"))
    assert "timer" in unique_violation_message(err)
    # a stopped entry beside it is fine
    db.add(TimeEntry(org_id=org, employee_id=me, category="admin", state="stopped",
                     ended_at=datetime(2026, 1, 1)))
    await db.commit()

    # the race: both taps passed the "already running?" check
    from backend.routers import timeclock

    async def _none(*a, **k):
        return None
    monkeypatch.setattr(timeclock, "_running_for", _none)
    r = await client.post("/api/timeclock/start", json={"category": "admin"}, headers=H)
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == "You already have a timer running — stop it first."


async def test_a_second_live_membership_on_one_asset_is_refused_and_a_race_gets_409(client, db, monkeypatch):
    H, org, me, loc = await _register(client)
    cid, did = await _client_and_asset(client, H)
    pid = await _plan(client, H)
    r = await client.post("/api/memberships", json={"plan_id": pid, "client_id": cid, "device_id": did,
                                                    "billing": "yearly"}, headers=H)
    assert r.status_code == 201, r.text
    today = date.today()
    row = dict(org_id=org, plan_id=pid, client_id=cid, device_id=did, billing="yearly", price=449,
               term_start=today, term_end=today, period_start=today, period_end=today)
    await _refused(db, Membership(status="active", **row))
    db.add(Membership(status="cancelled", **row))       # an ended one is history, not a clash
    await db.commit()

    from backend.services import memberships as svc

    async def _nobody(*a, **k):
        return []
    monkeypatch.setattr(svc, "live_on_device", _nobody)
    r = await client.post("/api/memberships", json={"plan_id": pid, "client_id": cid, "device_id": did,
                                                    "billing": "yearly"}, headers=H)
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == "This asset already has a live membership on that plan."


# ── DB-05 / FIN-17: document numbers ────────────────────────────────────────────────────────────

async def test_document_numbers_are_unique_per_shop(client, db):
    H, org, me, loc = await _register(client)
    H2, org2, me2, loc2 = await _register(client)
    db.add(Sale(org_id=org, location_id=loc, sale_number="S-1", seller_id=me))
    db.add(Sale(org_id=org2, location_id=loc2, sale_number="S-1", seller_id=me2))   # another shop: fine
    await db.commit()
    err = await _refused(db, Sale(org_id=org, location_id=loc, sale_number="S-1", seller_id=me))
    assert unique_violation_message(err) == "That document number was just used — please try again."

    db.add(StockTransfer(org_id=org, transfer_number="T-1", from_location_id=loc, to_location_id=loc))
    await db.commit()
    await _refused(db, StockTransfer(org_id=org, transfer_number="T-1", from_location_id=loc, to_location_id=loc))
    for model in (WriteOff, PurchaseReturn, Posting):
        db.add(model(org_id=org, number="D-1"))
        db.add(model(org_id=org, number=None))
        db.add(model(org_id=org, number=None))          # documents without a number don't clash
        await db.commit()
        await _refused(db, model(org_id=org, number="D-1"))


async def test_a_sale_skips_a_number_already_issued_and_a_race_gets_409(client, db, monkeypatch):
    H, org, me, loc = await _register(client)
    sale = {"lines": [{"kind": "service", "description": "Diagnostics", "qty": 1, "unit_price": 10}]}
    r = await client.post("/api/sales", json=sale, headers=H)
    assert r.status_code == 201, r.text
    first = (await db.execute(select(Sale.sale_number).where(Sale.org_id == org))).scalar_one()
    # The owner re-points the counter (Settings › Numbering): the next number is one already issued.
    from backend.models import NumberSequence
    await db.execute(update(NumberSequence).where(NumberSequence.org_id == org,
                                                  NumberSequence.doc_type == "sale").values(next_value=1))
    await db.commit()
    r = await client.post("/api/sales", json=sale, headers=H)
    assert r.status_code == 201, r.text
    numbers = (await db.execute(select(Sale.sale_number).where(Sale.org_id == org))).scalars().all()
    assert len(set(numbers)) == 2 and first in numbers

    # A race past the skip (two checkouts rendered the same number): 409, not 500.
    from backend.services import numbering

    async def _free(*a, **k):
        return False
    monkeypatch.setattr(numbering, "_number_taken", _free)
    await db.execute(update(NumberSequence).where(NumberSequence.org_id == org,
                                                  NumberSequence.doc_type == "sale").values(next_value=1))
    await db.commit()
    r = await client.post("/api/sales", json=sale, headers=H)
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == "That document number was just used — please try again."


# ── DB-06: coupon and gift-card codes ───────────────────────────────────────────────────────────

async def test_coupon_and_gift_card_codes_are_unique_per_shop(client, db):
    H, org, me, loc = await _register(client)
    H2, org2, *_ = await _register(client)
    db.add(Coupon(org_id=org, code="SAVE10", kind="pct", value=10))
    db.add(Coupon(org_id=org2, code="SAVE10", kind="pct", value=10))
    db.add(GiftCard(org_id=org, code="GC-ABC123", initial_value=10, balance=10))
    await db.commit()
    err = await _refused(db, Coupon(org_id=org, code="SAVE10", kind="pct", value=5))
    assert unique_violation_message(err) == "A coupon with that code already exists."
    err = await _refused(db, GiftCard(org_id=org, code="GC-ABC123", initial_value=5, balance=5))
    assert unique_violation_message(err) == "A gift card with that code already exists."


# ── DB-07: CHECK constraints ────────────────────────────────────────────────────────────────────

async def test_check_constraints_refuse_bad_signs_and_made_up_values(client, db):
    H, org, me, loc = await _register(client)
    cid, did = await _client_and_asset(client, H)
    pid = await _plan(client, H)
    pr = (await client.post("/api/catalog/products", json={"name": "Filter"}, headers=H)).json()["id"]
    today = date.today()
    ms = dict(org_id=org, plan_id=pid, client_id=cid, billing="yearly", price=1, status="active",
              term_start=today, term_end=today, period_start=today, period_end=today)
    bad = [
        StockLevel(part_id=pr, location_id=loc, qty_available=-1),
        StockLevel(part_id=pr, location_id=loc, qty_available=1, qty_reserved=-2),
        GiftCard(org_id=org, code="GC-NEG", initial_value=10, balance=-50),
        Coupon(org_id=org, code="NEG", kind="pct", value=-5),
        Coupon(org_id=org, code="KIND", kind="bogus", value=5),
        Coupon(org_id=org, code="OVER", kind="pct", value=5, uses=5, max_uses=1),
        Coupon(org_id=org, code="MIN", kind="pct", value=5, min_total=-1),
        Invoice(org_id=org, invoice_number="INV-X1", amount_paid=-10),
        TimeEntry(org_id=org, employee_id=me, state="whatever", ended_at=datetime(2026, 1, 1)),
        TimeEntry(org_id=org, employee_id=me, category="nap", state="stopped", ended_at=datetime(2026, 1, 1)),
        TimeEntry(org_id=org, employee_id=me, state="stopped", duration_sec=-60, ended_at=datetime(2026, 1, 1)),
        Membership(**{**ms, "status": "made_up"}),
        Membership(**{**ms, "billing": "weekly"}),
        Membership(**{**ms, "price": -1}),
        StockTransfer(org_id=org, transfer_number="T-9", from_location_id=loc, to_location_id=loc, status="lost"),
        PurchaseReturn(org_id=org, status="whatever"),
        PurchaseReturn(org_id=org, settlement="barter"),
        WriteOff(org_id=org, status="whatever"),
        Posting(org_id=org, status="whatever"),
    ]
    for row in bad:
        err = await _refused(db, row)
        assert "CHECK" in str(err.orig).upper() or "check constraint" in str(err.orig), (row, err)
        assert unique_violation_message(err) is None          # a bad value is a bug: it stays a 500

    # the edges that are allowed
    db.add_all([
        StockLevel(part_id=pr, location_id=loc, qty_available=0, qty_reserved=0),
        GiftCard(org_id=org, code="GC-ZERO", initial_value=10, balance=0),
        Coupon(org_id=org, code="SPENT", kind="fixed", value=5, uses=1, max_uses=1),
        Coupon(org_id=org, code="UNLIM", kind="pct", value=5, uses=40, max_uses=0),
        Membership(**{**ms, "status": "lapsed", "device_id": did}),
    ])
    await db.commit()
    # float dust from SQL arithmetic (0.3 − 0.1 − 0.2) is not "negative stock"
    row = (await db.execute(select(StockLevel).where(StockLevel.part_id == pr))).scalars().first()
    await db.execute(update(StockLevel).where(StockLevel.id == row.id).values(qty_available=0.3))
    await db.execute(update(StockLevel).where(StockLevel.id == row.id)
                     .values(qty_available=StockLevel.qty_available - 0.1))
    await db.execute(update(StockLevel).where(StockLevel.id == row.id)
                     .values(qty_available=StockLevel.qty_available - 0.2))
    await db.commit()


async def test_returning_customer_coupon_refuses_a_negative_minimum(client):
    H, *_ = await _register(client)
    cid = (await client.post("/api/clients", json={"name": "Pat", "phone": "5550101"}, headers=H)).json()["id"]
    r = await client.post(f"/api/clients/{cid}/coupons", json={"kind": "pct", "value": 10, "min_total": -5},
                          headers=H)
    assert r.status_code == 422, r.text


# ── the migration repairs existing data first ───────────────────────────────────────────────────

REPO = Path(__file__).resolve().parents[2]
W2_REVISION = "w2b01"


def _alembic(path, target):
    env = dict(os.environ, ENVIRONMENT="test", SEED_DEMO="false", DATABASE_URL=f"sqlite+aiosqlite:///{path}")
    env.pop("TEST_DATABASE_URL", None)
    return subprocess.run([sys.executable, "-m", "alembic", "-c", "backend/alembic.ini", "upgrade", target],
                          cwd=REPO, env=env, capture_output=True, text=True, timeout=600)


_VALID = {"status": {"stock_transfers": "draft", "write_offs": "posted", "purchase_returns": "posted",
                     "postings": "posted", "memberships": "active"},
          "settlement": "balance", "source": "timer", "billing": "yearly", "kind": "pct"}


def _insert(con, table, **vals):
    """Insert a row, filling NOT NULL columns the test doesn't care about with a harmless value."""
    for _cid, name, typ, notnull, default, _pk in con.execute(f"PRAGMA table_info({table})").fetchall():
        if name in vals or not notnull or default is not None:
            continue
        valid = _VALID.get(name)
        if isinstance(valid, dict):
            valid = valid.get(table)
        if valid is not None:
            vals[name] = valid
            continue
        t = (typ or "").upper()
        vals[name] = (0 if any(k in t for k in ("INT", "NUM", "FLOAT", "REAL", "BOOL", "DEC"))
                      else "2026-01-01 00:00:00" if ("DATE" in t or "TIME" in t) else f"x-{uuid.uuid4().hex[:6]}")
    cols = ", ".join(vals)
    con.execute(f"INSERT INTO {table} ({cols}) VALUES ({', '.join('?' for _ in vals)})", list(vals.values()))


def test_migration_repairs_duplicates_and_bad_values_before_adding_the_rules(tmp_path):
    path = tmp_path / "w2.db"
    r = _alembic(path, "srch01")
    assert r.returncode == 0, r.stderr[-3000:]
    con = sqlite3.connect(path)       # foreign keys off: rows for ids without parent rows
    t0 = "2026-11-01 09:00:00"
    # three open drawers at one location (the M10 triple click), one open elsewhere
    for i, opened in enumerate(("2026-11-01 09:00:00", "2026-11-01 09:00:01", "2026-11-01 09:00:02")):
        _insert(con, "cash_shifts", id=f"cs{i}", location_id="L1", opened_by_id="E1", opening_float=100,
                opened_at=opened, closed_at=None)
    _insert(con, "cash_shifts", id="cs-other", location_id="L2", opened_by_id="E1", opening_float=50,
            opened_at=t0, closed_at=None)
    _insert(con, "cash_drawer_movements", id="mv0", org_id="O1", shift_id="cs0", direction="in", amount=5,
            note="float top-up", created_at=t0)
    _insert(con, "payments", id="pay1", amount=20, kind="payment", method="cash", shift_id="cs1", paid_at=t0)
    # two running timers for one person (double tap), a third person with one
    _insert(con, "time_entries", id="te0", org_id="O1", employee_id="E1", state="running", ended_at=None,
            started_at="2026-11-01 08:00:00", duration_sec=0, category="job")
    _insert(con, "time_entries", id="te1", org_id="O1", employee_id="E1", state="running", ended_at=None,
            started_at="2026-11-01 08:30:00", duration_sec=0, category="job")
    _insert(con, "time_entries", id="te2", org_id="O1", employee_id="E2", state="running", ended_at=None,
            started_at=t0, duration_sec=0, category="job")
    # two live memberships, same plan, same asset
    for i, made in enumerate(("2026-10-01 00:00:00", "2026-10-02 00:00:00")):
        _insert(con, "memberships", id=f"m{i}", org_id="O1", plan_id="P1", client_id="C1", device_id="D1",
                status="active", billing="yearly", price=10, created_at=made)
    # duplicate numbers and codes
    for i in range(3):
        _insert(con, "sales", id=f"s{i}", org_id="O1", location_id="L1", sale_number="S-00042",
                seller_id="E1", created_at=f"2026-11-0{i + 1} 00:00:00")
    _insert(con, "sales", id="s-taken", org_id="O1", location_id="L1", sale_number="S-00042-2",
            seller_id="E1", created_at="2026-11-05 00:00:00")
    _insert(con, "sales", id="s-other-shop", org_id="O2", location_id="L9", sale_number="S-00042",
            seller_id="E9", created_at=t0)
    _insert(con, "sales", id="s-long1", org_id="O1", location_id="L1", sale_number="ABCDEFGHIJKLMNOPQRST",
            seller_id="E1", created_at="2026-11-01 00:00:00")
    _insert(con, "sales", id="s-long2", org_id="O1", location_id="L1", sale_number="ABCDEFGHIJKLMNOPQRST",
            seller_id="E1", created_at="2026-11-02 00:00:00")
    for tbl, col in (("stock_transfers", "transfer_number"), ("write_offs", "number"),
                     ("purchase_returns", "number"), ("postings", "number")):
        for i in range(2):
            _insert(con, tbl, id=f"{tbl}{i}", org_id="O1", **{col: "D-7"},
                    created_at=f"2026-11-0{i + 1} 00:00:00")
    for i in range(2):
        _insert(con, "coupons", id=f"cp{i}", org_id="O1", code="SAVE10", kind="pct", value=10, uses=i,
                max_uses=0, created_at=f"2026-11-0{i + 1} 00:00:00")
        _insert(con, "gift_cards", id=f"gc{i}", org_id="O1", code="GC-AAAAAA", initial_value=10, balance=10,
                created_at=f"2026-11-0{i + 1} 00:00:00")
    # values the new CHECKs refuse
    _insert(con, "stock_levels", id="sl-neg", part_id="PR1", location_id="L1", qty_available=-3, qty_reserved=-1)
    _insert(con, "coupons", id="cp-over", org_id="O1", code="ONCE", kind="pct", value=10, uses=3, max_uses=1)
    _insert(con, "gift_cards", id="gc-neg", org_id="O1", code="GC-NEG", initial_value=10, balance=-4)
    _insert(con, "time_entries", id="te-odd", org_id="O1", employee_id="E3", state="paused", category="lunch",
            ended_at=t0, started_at=t0, duration_sec=-5)
    con.commit()
    con.close()

    r = _alembic(path, W2_REVISION)
    assert r.returncode == 0, r.stderr[-4000:]
    log = r.stderr + r.stdout
    con = sqlite3.connect(path)
    q = lambda sql, *a: con.execute(sql, a).fetchall()      # noqa: E731

    # drawers: the newest stays open (it's the one the till shows); the others are merged into it
    assert q("SELECT id FROM cash_shifts WHERE location_id='L1' AND closed_at IS NULL") == [("cs2",)]
    assert q("SELECT count(*) FROM cash_shifts WHERE location_id='L2' AND closed_at IS NULL") == [(1,)]
    assert q("SELECT shift_id FROM cash_drawer_movements WHERE id='mv0'") == [("cs2",)]
    assert q("SELECT shift_id FROM payments WHERE id='pay1'") == [("cs2",)]
    closed = q("SELECT closing_float, expected_cash FROM cash_shifts WHERE id IN ('cs0','cs1')")
    assert all(float(c) == 100 and float(e) == 100 for c, e in closed)     # balanced: no false variance
    assert "cs0" in log and "cs1" in log
    # timers: the older one ends when the newer started, flagged for a manager
    assert q("SELECT state, substr(ended_at, 1, 19), duration_sec, needs_review FROM time_entries "
             "WHERE id='te0'") == [("stopped", "2026-11-01 08:30:00", 1800, 1)]
    assert q("SELECT state FROM time_entries WHERE id IN ('te1','te2') ORDER BY id") == [("running",), ("running",)]
    # memberships: the oldest stays live; the later one is cancelled as a duplicate
    assert q("SELECT id, status, ended_reason FROM memberships ORDER BY id") == [
        ("m0", "active", None), ("m1", "cancelled", "duplicate")]
    # numbers and codes: the first keeps its number, the others get a free suffix
    assert q("SELECT id, sale_number FROM sales WHERE org_id='O1' ORDER BY id") == [
        ("s-long1", "ABCDEFGHIJKLMNOPQRST"), ("s-long2", "ABCDEFGHIJKLMNOPQR-2"),
        ("s-taken", "S-00042-2"), ("s0", "S-00042"), ("s1", "S-00042-3"), ("s2", "S-00042-4")]
    assert q("SELECT sale_number FROM sales WHERE id='s-other-shop'") == [("S-00042",)]
    assert q("SELECT transfer_number FROM stock_transfers ORDER BY id") == [("D-7",), ("D-7-2",)]
    for tbl in ("write_offs", "purchase_returns", "postings"):
        assert q(f"SELECT number FROM {tbl} ORDER BY id") == [("D-7",), ("D-7-2",)]
    assert q("SELECT code FROM coupons WHERE code LIKE 'SAVE10%' ORDER BY id") == [("SAVE10",), ("SAVE10-2",)]
    assert q("SELECT code FROM gift_cards WHERE code LIKE 'GC-A%' ORDER BY id") == [("GC-AAAAAA",), ("GC-AAAAAA-2",)]
    # bad values brought inside the rules
    assert q("SELECT qty_available, qty_reserved FROM stock_levels WHERE id='sl-neg'") == [(0, 0)]
    assert q("SELECT count(*) FROM stock_movements WHERE part_id='PR1' AND qty=3") == [(1,)]
    assert q("SELECT uses, max_uses FROM coupons WHERE id='cp-over'") == [(3, 3)]
    assert q("SELECT balance FROM gift_cards WHERE id='gc-neg'") == [(0,)]
    assert q("SELECT state, category, duration_sec, needs_review FROM time_entries WHERE id='te-odd'") == [
        ("stopped", "other", 0, 1)]
    assert "S-00042-3" in log and "SAVE10-2" in log and "sl-neg" in log

    # and the rules are now in force on the migrated database
    con.execute("PRAGMA foreign_keys=OFF")
    with pytest.raises(sqlite3.IntegrityError):
        _insert(con, "cash_shifts", id="cs-dup", location_id="L1", opened_by_id="E1", closed_at=None)
    with pytest.raises(sqlite3.IntegrityError):
        _insert(con, "sales", id="s-dup", org_id="O1", location_id="L1", sale_number="S-00042", seller_id="E1")
    with pytest.raises(sqlite3.IntegrityError):
        _insert(con, "stock_levels", id="sl-bad", part_id="PR2", location_id="L1", qty_available=-1)
    # the expression and partial indexes on the rebuilt tables survived
    idx = {n for (n,) in q("SELECT name FROM sqlite_master WHERE type='index'")}
    assert {"uq_stock_levels_part_loc_unbinned", "uq_stock_levels_part_bin", "uq_cash_shifts_open_location",
            "uq_time_entries_running_employee", "uq_memberships_live_device_plan"} <= idx
    con.close()


def test_migration_stops_with_a_list_when_a_value_has_no_safe_repair(tmp_path):
    path = tmp_path / "w2-refuse.db"
    assert _alembic(path, "srch01").returncode == 0
    con = sqlite3.connect(path)
    _insert(con, "stock_transfers", id="t1", org_id="O1", transfer_number="T-1", status="lost_in_post")
    _insert(con, "coupons", id="c1", org_id="O1", code="A", kind="pct", value=1)
    _insert(con, "coupons", id="c2", org_id="O1", code="A", kind="pct", value=1)
    con.commit()
    con.close()
    r = _alembic(path, W2_REVISION)
    assert r.returncode != 0
    assert "stock_transfers.status = 'lost_in_post' on 1 row(s)" in r.stderr
    con = sqlite3.connect(path)          # nothing was changed
    assert con.execute("SELECT code FROM coupons ORDER BY id").fetchall() == [("A",), ("A",)]
    assert con.execute("SELECT version_num FROM alembic_version").fetchall() == [("srch01",)]
    con.close()
