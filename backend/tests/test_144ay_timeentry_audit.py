"""144ay — append-only time-entry audit trail (ADR 144ax Option B).

Every manual create / edit / approve / reopen / void writes an immutable audit row with
who-changed-what (old→new), readable via GET /timeclock/entries/{id}/audit.
"""
from datetime import datetime, timedelta

import pytest


async def _manual_entry(client, h):
    start = datetime(2026, 9, 1, 9, 0, 0)
    end = start + timedelta(hours=2)
    r = await client.post("/api/timeclock/entries", json={
        "category": "admin", "started_at": start.isoformat(), "ended_at": end.isoformat(),
        "note": "orig"}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.mark.asyncio
async def test_audit_trail_records_every_change(client, tenant):
    eid = await _manual_entry(client, tenant)

    # create row present
    trail = (await client.get(f"/api/timeclock/entries/{eid}/audit", headers=tenant)).json()["items"]
    assert any(a["action"] == "create" for a in trail)

    # edit the note + end time → two edit rows with old→new
    new_end = datetime(2026, 9, 1, 12, 0, 0).isoformat()
    r = await client.patch(f"/api/timeclock/entries/{eid}", json={"note": "fixed", "ended_at": new_end}, headers=tenant)
    assert r.status_code == 200, r.text
    trail = (await client.get(f"/api/timeclock/entries/{eid}/audit", headers=tenant)).json()["items"]
    note_edit = next(a for a in trail if a["action"] == "edit" and a["field"] == "note")
    assert note_edit["old"] == "orig" and note_edit["new"] == "fixed"
    assert any(a["action"] == "edit" and a["field"] == "ended_at" for a in trail)

    # approve → reopen → void, each audited
    assert (await client.post(f"/api/timeclock/entries/{eid}/approve", headers=tenant)).status_code == 200
    assert (await client.post(f"/api/timeclock/entries/{eid}/reopen", headers=tenant)).status_code == 200
    assert (await client.delete(f"/api/timeclock/entries/{eid}", headers=tenant)).status_code == 200

    trail = (await client.get(f"/api/timeclock/entries/{eid}/audit", headers=tenant)).json()["items"]
    actions = [a["action"] for a in trail]
    for expected in ("create", "edit", "approve", "reopen", "void"):
        assert expected in actions, f"missing {expected} in {actions}"
    # append-only + chronological: timestamps never decrease
    ats = [a["at"] for a in trail]
    assert ats == sorted(ats)


@pytest.mark.asyncio
async def test_unchanged_edit_writes_no_row(client, tenant):
    """A PATCH that doesn't actually change a field must not spam the trail."""
    eid = await _manual_entry(client, tenant)
    before = len((await client.get(f"/api/timeclock/entries/{eid}/audit", headers=tenant)).json()["items"])
    await client.patch(f"/api/timeclock/entries/{eid}", json={"note": "orig"}, headers=tenant)  # same value
    after = len((await client.get(f"/api/timeclock/entries/{eid}/audit", headers=tenant)).json()["items"])
    assert after == before
