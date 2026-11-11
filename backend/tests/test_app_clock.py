"""The app never takes "today" from the machine's clock (hardening council Q2).

`date.today()` is the production host's local date. On a host not set to UTC it drifts a day from
both the UTC day (platform billing, GS1 dates) and the shop's day (what customers and staff
see). Each case freezes the app clock (services/dates._clock) at a moment where the host's date,
the UTC date and the shop's date can differ, so the answer can only come from the right clock.
"""
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from backend.models import Organisation


def _freeze(monkeypatch, wall: str, tz: str = "UTC"):
    from backend.services import dates
    at = datetime.fromisoformat(wall).replace(tzinfo=ZoneInfo(tz)).astimezone(timezone.utc)
    monkeypatch.setattr(dates, "_clock", lambda: at)


# ── platform dates: the UTC day ──────────────────────────────────────────────────────────────────

def test_a_platform_discount_lasts_to_the_end_of_its_utc_day(monkeypatch):
    from backend.services.admin_access import effective_discount
    org = Organisation(name="x", discount_pct=20, discount_until=date(2026, 3, 14))
    _freeze(monkeypatch, "2026-03-14 23:50")
    assert effective_discount(org) == 20
    _freeze(monkeypatch, "2026-03-15 00:10")
    assert effective_discount(org) == 0


def test_an_admins_discount_length_limit_counts_from_the_utc_day(monkeypatch):
    from backend.services.admin_access import over_discount_limit
    ctx = SimpleNamespace(is_owner=False, limits={"max_discount_pct": 50, "max_discount_months": 1})
    _freeze(monkeypatch, "2026-03-14 12:00")
    assert over_discount_limit(ctx, 10, date(2026, 3, 14) + timedelta(days=31)) is None
    assert over_discount_limit(ctx, 10, date(2026, 3, 14) + timedelta(days=32)) == "Longer than your 1-month limit"


def test_gs1_century_window_uses_the_app_clock(monkeypatch):
    from backend.services.scan_codes import _gs1_date
    _freeze(monkeypatch, "2076-06-01 12:00")
    assert _gs1_date("260101") == date(2126, 1, 1)     # 50 years back is out of the window
    assert _gs1_date("770101") == date(2077, 1, 1)


# ── shop dates: the shop's day ───────────────────────────────────────────────────────────────────
# (memberships, supplier warranty and the pilot report: test_shop_clock_memberships.py)

@pytest.mark.asyncio
async def test_estimate_expiry_with_an_unknown_zone_falls_back_to_the_utc_day(db, monkeypatch):
    """public._expired: a shop whose zone name doesn't resolve reads the UTC day, as documented."""
    from backend.routers.public import _expired
    org = Organisation(name="x", timezone="Not/AZone")
    db.add(org)
    await db.commit()
    order = SimpleNamespace(is_estimate=True, estimate_valid_until=date(2026, 3, 14), org_id=org.id,
                            location_id=None)
    _freeze(monkeypatch, "2026-03-14 23:50")
    assert await _expired(db, order) is False
    _freeze(monkeypatch, "2026-03-15 00:10")
    assert await _expired(db, order) is True
