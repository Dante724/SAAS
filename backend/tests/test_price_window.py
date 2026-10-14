"""144hb — promotional window on price types: an expired promo doesn't apply."""
from backend.routers.pricing import _in_window
from types import SimpleNamespace
from datetime import datetime, timedelta


def test_in_window_bounds():
    now = datetime(2026, 6, 15)
    past = SimpleNamespace(starts_at=None, ends_at=datetime(2026, 6, 1))
    future = SimpleNamespace(starts_at=datetime(2026, 7, 1), ends_at=None)
    live = SimpleNamespace(starts_at=datetime(2026, 6, 1), ends_at=datetime(2026, 6, 30))
    always = SimpleNamespace(starts_at=None, ends_at=None)
    assert _in_window(past, now) is False
    assert _in_window(future, now) is False
    assert _in_window(live, now) is True
    assert _in_window(always, now) is True


async def test_price_type_carries_window(client, tenant):
    ends = (datetime.utcnow() + timedelta(days=7)).date().isoformat()
    r = await client.post("/api/pricing/types",
                          json={"name": "Summer promo", "markup_pct": 20, "ends_at": ends}, headers=tenant)
    assert r.status_code == 201, r.text
    got = r.json()
    assert got["ends_at"] is not None
    assert got["in_window"] is True
