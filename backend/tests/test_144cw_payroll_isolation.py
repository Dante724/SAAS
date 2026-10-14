"""144cw — tenant isolation on manual time entries. A manager must not be able to log time
against an employee that belongs to another org (would leak that employee's pay rates into the
caller's org and pollute payroll)."""
import uuid

import pytest


async def _register(client):
    email = f"pay-{uuid.uuid4().hex[:8]}@test.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Org B", "name": "Owner B", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    me = (await client.get("/api/auth/me", headers=h)).json()
    return h, me


@pytest.mark.asyncio
async def test_cannot_log_time_for_foreign_employee(client, tenant):
    # tenant = org A (owner/manager). Create a separate org B with its own employee.
    _hb, me_b = await _register(client)

    # org A manager tries to log time against org B's employee id
    r = await client.post("/api/timeclock/entries", json={
        "employee_id": me_b["id"], "category": "admin",
        "started_at": "2026-10-01T09:00:00", "ended_at": "2026-10-01T10:00:00",
    }, headers=tenant)
    assert r.status_code in (403, 404), f"foreign-employee time entry must be rejected, got {r.status_code}: {r.text}"
