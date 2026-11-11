"""Hardening council M12: table reports, their csv/xlsx exports and total profit took date_from /
date_to as naive UTC days (reports._parse_range), while the dashboard counts "today" on the shop's
clock (services/dates). A shop far from UTC asked for its own "today" and got the wrong rows.

The clock is frozen through dates._clock (the pattern of test_shop_timezone_today.py): Kiritimati
(UTC+14) is on the next day by 12:00 UTC; Honolulu (UTC-10) is still on the previous day at 05:00 UTC.
"""
import uuid
from datetime import datetime, timezone

import pytest

from backend.services import dates

pytestmark = pytest.mark.asyncio

AHEAD, BEHIND = "Pacific/Kiritimati", "Pacific/Honolulu"


def _freeze(monkeypatch, *args):
    at = datetime(*args, tzinfo=timezone.utc)
    monkeypatch.setattr(dates, "_clock", lambda: at)
    return at


async def _shop(client, tz):
    email = f"tz-{uuid.uuid4().hex[:8]}@t.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Far Away Repairs", "name": "Owner", "email": email,
        "password": "pw-test-12345", "timezone": tz})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _job(client, H):
    cid = (await client.post("/api/clients", json={"name": "Ana"}, headers=H)).json()["id"]
    r = await client.post("/api/orders", json={"client_id": cid, "lines": [
        {"line_type": "labour", "description": "Fix", "qty": 1, "unit_price": 100}]}, headers=H)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _rows(client, H, day):
    r = await client.get(f"/api/reports/run/wo_created?date_from={day}&date_to={day}", headers=H)
    assert r.status_code == 200, r.text
    return r.json()["rows"]


async def _csv_lines(client, H, day):
    r = await client.get(f"/api/reports/export-csv/wo_created?date_from={day}&date_to={day}", headers=H)
    assert r.status_code == 200, r.text
    return [ln for ln in r.text.splitlines()[1:] if ln.strip()]


async def _profit_jobs(client, H, day):
    r = await client.get(f"/api/reports/total-profit?date_from={day}&date_to={day}", headers=H)
    assert r.status_code == 200, r.text
    body = r.json()
    return len(body.get("rows") or body.get("jobs") or []), body


async def test_report_day_follows_a_shop_ahead_of_utc(client, monkeypatch):
    """Kiritimati at 12:00 UTC on the 31st: the shop's clock says 02:00 on 1 Nov. A job created
    now belongs to the shop's 1 Nov, not to the UTC 31 Oct."""
    H = await _shop(client, AHEAD)
    _freeze(monkeypatch, 2026, 10, 31, 12, 0)
    shop_today = dates.today_in(AHEAD)
    assert str(shop_today) == "2026-11-01"
    await _job(client, H)

    assert len(await _rows(client, H, "2026-11-01")) == 1, "the shop's own today lists the job"
    assert len(await _rows(client, H, "2026-10-31")) == 0, "the UTC day doesn't"
    assert len(await _csv_lines(client, H, "2026-11-01")) == 1
    assert len(await _csv_lines(client, H, "2026-10-31")) == 0


async def test_report_day_follows_a_shop_behind_utc(client, monkeypatch):
    """Honolulu at 05:00 UTC on the 31st: the shop's clock says 19:00 on 30 Oct."""
    H = await _shop(client, BEHIND)
    _freeze(monkeypatch, 2026, 10, 31, 5, 0)
    assert str(dates.today_in(BEHIND)) == "2026-10-30"
    await _job(client, H)

    assert len(await _rows(client, H, "2026-10-30")) == 1, "the shop's own today lists the job"
    assert len(await _rows(client, H, "2026-10-31")) == 0, "the UTC day doesn't"


async def test_total_profit_window_follows_the_shop_day(client, monkeypatch):
    H = await _shop(client, AHEAD)
    _freeze(monkeypatch, 2026, 10, 31, 12, 0)
    oid = await _job(client, H)
    # Close it so it counts as a successful job on the shop's 1 Nov.
    await client.post("/api/statuses", json={"name": "Done TZ", "key": "done-tz",
                                             "status_group": "closed", "is_terminal": True}, headers=H)
    r = await client.patch(f"/api/orders/{oid}/status", json={"status": "done-tz"}, headers=H)
    assert r.status_code == 200, r.text
    n_shop, body = await _profit_jobs(client, H, "2026-11-01")
    n_utc, _ = await _profit_jobs(client, H, "2026-10-31")
    assert (n_shop, n_utc) == (1, 0), {"shop_day": n_shop, "utc_day": n_utc, "keys": list(body)}


async def test_a_utc_shop_is_unchanged(client, monkeypatch):
    H = await _shop(client, "UTC")
    _freeze(monkeypatch, 2026, 10, 31, 12, 0)
    await _job(client, H)
    assert len(await _rows(client, H, "2026-10-31")) == 1
    assert len(await _rows(client, H, "2026-11-01")) == 0
    # A date-only "to" still covers the whole end day, and the default window still answers.
    r = await client.get("/api/reports/run/wo_created?date_from=2026-10-01&date_to=2026-10-31", headers=H)
    assert r.status_code == 200 and len(r.json()["rows"]) == 1
    r = await client.get("/api/reports/run/wo_created", headers=H)
    assert r.status_code == 200 and len(r.json()["rows"]) == 1
