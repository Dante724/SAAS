"""Robustness regression: a malformed date in a request body now returns a clean 400
(via services.dates.parse_date/parse_dt) instead of an unhandled 500."""
import pytest


@pytest.mark.asyncio
async def test_schedule_bad_day_is_400(client, tenant):
    r = await client.post("/api/schedules", headers=tenant, json={
        "employee_id": "whoever", "day": "not-a-real-date", "kind": "shift",
        "start": "09:00", "end": "17:00"})
    assert r.status_code == 400, r.text
    assert "day" in r.text.lower()
