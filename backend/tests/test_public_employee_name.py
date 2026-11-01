"""Staff names customers see: the booking page and the {technician} message variable follow the
org's choice (full | first | custom → Employee.public_name)."""
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from backend.models import Employee, Organisation
from backend.services.public_name import public_staff_name


def test_name_forms():
    e = SimpleNamespace(name="Sam Ortega", public_name="Sam O.")
    assert public_staff_name(e, "full") == "Sam Ortega"
    assert public_staff_name(e, None) == "Sam Ortega"
    assert public_staff_name(e, "first") == "Sam"
    assert public_staff_name(e, "custom") == "Sam O."
    assert public_staff_name(SimpleNamespace(name="Sam Ortega", public_name="  "), "custom") == "Sam Ortega"
    assert public_staff_name(None, "full") == ""


async def _setup(client, tenant, db):
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    org = (await db.execute(select(Organisation).where(Organisation.id == me["org_id"]))).scalar_one()
    org.appointment_enabled = True
    org.subdomain = "names-shop"
    emp = (await db.execute(select(Employee).where(Employee.id == me["id"]))).scalar_one()
    emp.name, emp.public_name = "Alex Morgan", "Alex M."
    await db.commit()
    return emp


async def _staff_names(client):
    r = await client.get("/api/public/appointment/names-shop")
    assert r.status_code == 200, r.text
    return [s["name"] for s in r.json()["staff"]]


@pytest.mark.asyncio
async def test_booking_page_follows_setting(client, tenant, db):
    await _setup(client, tenant, db)
    assert "Alex Morgan" in await _staff_names(client)          # default: full name

    for mode, shown in (("first", "Alex"), ("custom", "Alex M."), ("full", "Alex Morgan")):
        r = await client.put("/api/staff/public-pages", json={"public_employee_name": mode}, headers=tenant)
        assert r.status_code == 200, r.text
        assert (await client.get("/api/staff/public-pages", headers=tenant)).json()["public_employee_name"] == mode
        assert shown in await _staff_names(client)


@pytest.mark.asyncio
async def test_bad_mode_rejected(client, tenant):
    r = await client.put("/api/staff/public-pages", json={"public_employee_name": "nickname"}, headers=tenant)
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_technician_message_variable(client, tenant, db):
    emp = await _setup(client, tenant, db)
    oid = (await client.post("/api/orders", json={"complaint": "Screen", "assigned_to_id": emp.id},
                             headers=tenant)).json()["id"]
    await client.put("/api/staff/public-pages", json={"public_employee_name": "first"}, headers=tenant)
    from backend.models import Order
    from backend.services.message_vars import order_values
    o = (await db.execute(select(Order).where(Order.id == oid))).scalar_one()
    vals = await order_values(db, o.org_id, o, "Your tech is {technician}")
    assert vals["technician"] == "Alex"
