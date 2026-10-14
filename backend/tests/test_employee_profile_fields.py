"""Build 144k — richer employee profile fields (position, hire date, ITN, note…)."""
import pytest


@pytest.mark.asyncio
async def test_create_and_read_profile_fields(client, tenant):
    r = await client.post("/api/staff/employees", json={
        "name": "Priya Patel", "email": "priya-prof@test.dev", "password": "pw-emp-12345",
        "phone": "+91-99999", "position": "Senior Technician", "hired_at": "2021-05-26",
        "tax_id": "ITN-12345", "public_name": "Priya P.", "note": "Great with diagnostics"},
        headers=tenant)
    assert r.status_code == 201, r.text
    eid = r.json()["id"]

    row = next(e for e in (await client.get("/api/staff/employees", headers=tenant)).json()
               if e["id"] == eid)
    assert row["position"] == "Senior Technician"
    assert row["hired_at"] == "2021-05-26"
    assert row["tax_id"] == "ITN-12345"
    assert row["public_name"] == "Priya P."
    assert row["note"] == "Great with diagnostics"


@pytest.mark.asyncio
async def test_edit_profile_fields(client, tenant):
    eid = (await client.post("/api/staff/employees", json={
        "name": "Edit Me", "email": "edit-prof@test.dev", "password": "pw-emp-12345"},
        headers=tenant)).json()["id"]

    upd = await client.patch(f"/api/staff/employees/{eid}", json={
        "position": "Manager", "hired_at": "2020-01-15", "note": "Promoted"}, headers=tenant)
    assert upd.status_code == 200, upd.text

    row = next(e for e in (await client.get("/api/staff/employees", headers=tenant)).json()
               if e["id"] == eid)
    assert row["position"] == "Manager"
    assert row["hired_at"] == "2020-01-15"
    assert row["note"] == "Promoted"

    # a blank / bad hire date clears cleanly rather than erroring
    upd2 = await client.patch(f"/api/staff/employees/{eid}", json={"hired_at": ""}, headers=tenant)
    assert upd2.status_code == 200
    row2 = next(e for e in (await client.get("/api/staff/employees", headers=tenant)).json()
                if e["id"] == eid)
    assert row2["hired_at"] is None


@pytest.mark.asyncio
async def test_profile_fields_optional(client, tenant):
    # omitting the new fields still works and returns nulls
    eid = (await client.post("/api/staff/employees", json={
        "name": "Minimal", "email": "min-prof@test.dev", "password": "pw-emp-12345"},
        headers=tenant)).json()["id"]
    row = next(e for e in (await client.get("/api/staff/employees", headers=tenant)).json()
               if e["id"] == eid)
    assert row["position"] is None and row["note"] is None and row["hired_at"] is None
