"""'Today' is the shop's calendar day, not the server's (audit: timestamps item).

Timestamps stay naive UTC (see docs/ADR-tz-timestamps.md); every "what day is it" decision goes
through services/dates.py with the org's timezone. The clock is frozen through dates._clock so the
day boundary is deterministic: Kiritimati (UTC+14) is already on the next day for most of the UTC
day, Pago Pago (UTC-11) is still on the previous one.
"""
import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.services import dates

pytestmark = pytest.mark.asyncio

AHEAD, BEHIND = "Pacific/Kiritimati", "Pacific/Pago_Pago"


def _freeze(monkeypatch, *args):
    at = datetime(*args, tzinfo=timezone.utc)
    monkeypatch.setattr(dates, "_clock", lambda: at)
    return at


# ── helpers ──────────────────────────────────────────────────────────────────
async def test_now_utc_is_naive_utc(monkeypatch):
    _freeze(monkeypatch, 2026, 10, 31, 12, 0)
    assert dates.now_utc() == datetime(2026, 10, 31, 12, 0)
    assert dates.now_utc().tzinfo is None          # stores into / compares with the naive columns


async def test_today_follows_the_shop_clock(monkeypatch):
    _freeze(monkeypatch, 2026, 10, 31, 12, 0)
    assert dates.today_in(AHEAD) == date(2026, 11, 1)
    assert dates.today_in(BEHIND) == date(2026, 10, 31)
    assert dates.today_in("UTC") == date(2026, 10, 31)
    _freeze(monkeypatch, 2026, 10, 31, 5, 0)
    assert dates.today_in(BEHIND) == date(2026, 10, 30)
    # Unknown or empty zone falls back to UTC rather than failing the request.
    assert dates.today_in("Not/AZone") == date(2026, 10, 31)
    assert dates.today_in(None) == date(2026, 10, 31)
    assert dates.org_today(SimpleNamespace(timezone=AHEAD)) == date(2026, 10, 31)   # 19:00 there


async def test_day_start_in_utc():
    assert dates.day_start_utc(date(2026, 11, 1), AHEAD) == datetime(2026, 10, 31, 10, 0)
    assert dates.day_start_utc(date(2026, 10, 31), BEHIND) == datetime(2026, 10, 31, 11, 0)
    assert dates.day_end_utc(date(2026, 10, 31), "UTC") == datetime(2026, 11, 1)
    # DST: London's 25 Oct 2026 is 25 hours long.
    london = dates.day_end_utc(date(2026, 10, 25), "Europe/London") - dates.day_start_utc(date(2026, 10, 25), "Europe/London")
    assert london == timedelta(hours=25)


async def test_subscription_end_is_a_utc_day_whatever_the_shop_timezone(monkeypatch):
    """sub_end is set by the platform; the tenant controls Organisation.timezone, so it must not
    be able to stretch (or shrink) its own subscription by picking a far zone."""
    from backend.services.auth import assert_org_active
    _freeze(monkeypatch, 2026, 10, 31, 12, 0)      # Kiritimati: 1 Nov already; Pago Pago: 31 Oct
    for tz in (AHEAD, BEHIND, "UTC"):
        assert_org_active(SimpleNamespace(timezone=tz, sub_status="active", sub_end=date(2026, 10, 31)))
        with pytest.raises(HTTPException) as e:
            assert_org_active(SimpleNamespace(timezone=tz, sub_status="active", sub_end=date(2026, 10, 30)))
        assert e.value.status_code == 403
    assert dates.platform_today() == date(2026, 10, 31)


async def test_payroll_cutoff_counts_back_from_given_day():
    from backend.services.scoping import payroll_cutoff
    role = SimpleNamespace(payroll_visibility_days=30)
    assert payroll_cutoff(role, date(2026, 11, 1)) == date(2026, 10, 2)
    assert payroll_cutoff(SimpleNamespace(payroll_visibility_days=0), date(2026, 11, 1)) is None


# ── API: payment date on a shop far from UTC ────────────────────────────────
async def _shop(client, tz):
    email = f"tz-{uuid.uuid4().hex[:8]}@t.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Far Away Repairs", "name": "Owner", "email": email,
        "password": "pw-test-12345", "timezone": tz})
    assert r.status_code == 201, r.text
    H = {"Authorization": f"Bearer {r.json()['access_token']}"}
    cid = (await client.post("/api/clients", json={"name": "Ana"}, headers=H)).json()["id"]
    acc = (await client.post("/api/cash/accounts", json={"name": "Till", "payment_option": "cash"},
                             headers=H)).json()["id"]
    r = await client.post("/api/finance/invoices", headers=H, json={
        "client_id": cid, "lines": [{"description": "Screen", "qty": 1, "unit_price": 100}]})
    assert r.status_code == 201, r.text
    return H, acc, r.json()["id"]


async def _no_retro_teammate(client, H):
    r = await client.post("/api/settings/rbac/roles", json={"template": "Full access", 
        "name": f"No retro {uuid.uuid4().hex[:4]}", "permissions": {"can_retroactive_cash": False}}, headers=H)
    assert r.status_code in (200, 201), r.text
    email = f"tm-{uuid.uuid4().hex[:8]}@t.dev"
    r = await client.post("/api/staff/employees", json={
        "name": "Desk", "email": email, "password": "pw-test-12345", "role_id": r.json()["id"]}, headers=H)
    assert r.status_code == 201, r.text
    tok = await client.post("/api/auth/token", data={"username": email, "password": "pw-test-12345"})
    return {"Authorization": f"Bearer {tok.json()['access_token']}"}


def _real_noon():
    """Noon UTC of the real current day: within 12h of the real clock, so tokens issued before
    the freeze stay valid, and every shop below is on a known side of the date line."""
    d = datetime.now(timezone.utc).date()
    return d, (d.year, d.month, d.day, 12, 0)


async def test_payment_on_shop_today_ahead_of_utc(client, monkeypatch):
    H, acc, iid = await _shop(client, AHEAD)
    T = await _no_retro_teammate(client, H)
    utc_day, noon = _real_noon()
    _freeze(monkeypatch, *noon)
    shop_day = utc_day + timedelta(days=1)          # Kiritimati is already tomorrow
    # The shop's today is not "the future", and not a retroactive entry either.
    r = await client.post(f"/api/finance/invoices/{iid}/pay", headers=T, json={
        "amount": 10, "method": "cash", "account_id": acc, "paid_at": shop_day.isoformat()})
    assert r.status_code == 200, r.text
    # The UTC date is the shop's yesterday: retroactive, so it needs the right.
    r = await client.post(f"/api/finance/invoices/{iid}/pay", headers=T, json={
        "amount": 10, "method": "cash", "account_id": acc, "paid_at": utc_day.isoformat()})
    assert r.status_code == 403, r.text


async def test_payment_on_utc_today_is_future_for_shop_behind(client, monkeypatch):
    H, acc, iid = await _shop(client, BEHIND)
    utc_day, _ = _real_noon()
    _freeze(monkeypatch, utc_day.year, utc_day.month, utc_day.day, 5, 0)   # Pago Pago: still yesterday
    r = await client.post(f"/api/finance/invoices/{iid}/pay", headers=H, json={
        "amount": 10, "method": "cash", "account_id": acc, "paid_at": utc_day.isoformat()})
    assert r.status_code == 422, r.text
    r = await client.post(f"/api/finance/invoices/{iid}/pay", headers=H, json={
        "amount": 10, "method": "cash", "account_id": acc,
        "paid_at": (utc_day - timedelta(days=1)).isoformat()})
    assert r.status_code == 200, r.text


async def test_cash_flow_floor_is_shop_midnight(client, monkeypatch, db):
    from sqlalchemy import select

    from backend.models import Employee
    from backend.routers.cash import cash_flow_floor
    H, _, _ = await _shop(client, AHEAD)
    me = (await client.get("/api/auth/me", headers=H)).json()
    emp = (await db.execute(select(Employee).where(Employee.id == me["id"]))).scalar_one()
    utc_day, noon = _real_noon()
    _freeze(monkeypatch, *noon)

    async def _no(*a, **k):
        return False
    monkeypatch.setattr("backend.routers.cash.has_perm", _no)
    # Kiritimati's today began at 10:00 UTC on the UTC calendar day.
    assert await cash_flow_floor(db, emp) == datetime(utc_day.year, utc_day.month, utc_day.day, 10, 0)


async def test_every_money_report_reads_a_date_only_range_as_the_shop_day(client, db):
    """A counter sale and a new client at 01:30 on 11 Nov in Kolkata are 20:00 UTC on 10 Nov.
    Asked for 11 Nov, the P&L, Total profit and a custom report all count them, and the P&L takes
    that day's expense record only. The P&L and custom reports used to read a date-only range as
    UTC days while Total profit and the Reports page read the shop's day, so near the shop's
    midnight two reports for the same day disagreed."""
    from decimal import Decimal

    from sqlalchemy import update

    from backend.models import Client, Expense, Sale
    from backend.services import custom_reports
    from backend.tests.test_b5_pos_quote import _product
    H, acc, _ = await _shop(client, "Asia/Kolkata")
    org = (await client.get("/api/auth/me", headers=H)).json()["org_id"]
    pid, _loc = await _product(client, H, db, qty=5, price=50)
    r = await client.post("/api/sales", json={"account_id": acc, "lines": [
        {"kind": "product", "ref_id": pid, "description": "Charger", "qty": 1, "unit_price": 50}]}, headers=H)
    assert r.status_code == 201, r.text
    late_evening_utc = datetime(2026, 11, 10, 20, 0)          # 01:30 on 11 Nov in Kolkata
    await db.execute(update(Sale).where(Sale.id == r.json()["id"]).values(created_at=late_evening_utc))
    await db.execute(update(Client).where(Client.org_id == org).values(created_at=late_evening_utc))
    db.add_all([Expense(org_id=org, description="Rent", amount=Decimal("7"), expense_date=date(2026, 11, 10)),
                Expense(org_id=org, description="Tea", amount=Decimal("3"), expense_date=date(2026, 11, 11))])
    await db.commit()

    async def figures(day):
        pnl = (await client.get(f"/api/finance/pnl?date_from={day}&date_to={day}", headers=H)).json()
        tp = (await client.get(f"/api/reports/total-profit?date_from={day}&date_to={day}", headers=H)).json()
        clients = await custom_reports.build_and_run(db, org, {"source": "clients", "columns": ["name"]}, day, day)
        return (pnl["revenue"], tp["totals"]["revenue"], pnl["opex"], pnl["date_from"], pnl["date_to"],
                clients["row_count"])

    assert await figures("2026-11-11") == (50.0, 50.0, 3.0, "2026-11-11", "2026-11-11", 1)
    assert await figures("2026-11-10") == (0.0, 0.0, 7.0, "2026-11-10", "2026-11-10", 0)
