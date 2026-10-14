"""Build 144y — optional time-based labour billing from the job clock."""
import pytest


async def _order(client, tenant):
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    cid = (await client.post("/api/clients", json={"name": "Rae"}, headers=tenant)).json()["id"]
    return (await client.post("/api/orders", json={
        "client_id": cid, "location_id": loc, "complaint": "diag"}, headers=tenant)).json()["id"]


async def _lines(client, tenant, oid):
    return (await client.get(f"/api/orders/{oid}", headers=tenant)).json()["lines"]


@pytest.mark.asyncio
async def test_toggle_default_off_and_persists(client, tenant):
    c = (await client.get("/api/staff/company", headers=tenant)).json()
    assert c["clock_labour_billing"] is False
    await client.put("/api/staff/company", json={"name": "Shop", "clock_labour_billing": True}, headers=tenant)
    assert (await client.get("/api/staff/company", headers=tenant)).json()["clock_labour_billing"] is True


@pytest.mark.asyncio
async def test_no_labour_line_when_toggle_off(client, tenant):
    oid = await _order(client, tenant)
    await client.post("/api/timeclock/start", json={"order_id": oid, "category": "job"}, headers=tenant)
    await client.post("/api/timeclock/stop", headers=tenant)
    # billing off by default → no auto labour line
    assert not any(ln["line_type"] == "labour" for ln in await _lines(client, tenant, oid))


@pytest.mark.asyncio
async def test_manual_entry_bills_labour_by_time(client, tenant):
    # turn the optional billing on
    await client.put("/api/staff/company", json={"name": "Shop", "clock_labour_billing": True}, headers=tenant)
    oid = await _order(client, tenant)
    me = (await client.get("/api/auth/me", headers=tenant)).json()

    # a manual 2-hour entry on the order (deterministic duration)
    r = await client.post("/api/timeclock/entries", json={
        "employee_id": me["id"], "order_id": oid, "category": "job",
        "started_at": "2026-08-01T09:00:00", "ended_at": "2026-08-01T11:00:00",
        "note": "Engine diagnosis"}, headers=tenant)
    assert r.status_code == 201, r.text
    eid = r.json()["id"]

    lines = await _lines(client, tenant, oid)
    labour = [ln for ln in lines if ln["line_type"] == "labour"]
    assert len(labour) == 1
    assert abs(labour[0]["qty"] - 2.0) < 0.01               # 2 tracked hours
    assert labour[0]["total"] == round(2.0 * labour[0]["unit_price"], 2)  # hours × rate
    assert "Engine diagnosis" in (labour[0]["description"] or "")

    # voiding the entry removes its labour line (idempotent, no double billing)
    await client.delete(f"/api/timeclock/entries/{eid}", headers=tenant)
    assert not any(ln["line_type"] == "labour" for ln in await _lines(client, tenant, oid))
