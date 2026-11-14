"""Owner decision 10 ("Yes, keep it (a)"): an employee email is unique across all of Tekly, not per
shop. Sign-in, password reset and invites find a person by email alone, with no shop picker, so one
email must always resolve to exactly one person. These tests lock that in. A "choose your shop" step is
a post-beta follow-up; if it is ever built, these tests are the ones to change, on purpose.
"""
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from backend.models import AuthToken, Employee, Organisation

PW = "pw-test-12345"


async def _shop(client, email: str, name: str = "Shop") -> dict:
    r = await client.post("/api/auth/register", json={"org_name": name, "name": "Owner", "email": email, "password": PW})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _people(db, email: str) -> list[Employee]:
    db.expire_all()
    return list((await db.execute(select(Employee).where(func.lower(Employee.email) == email.lower())
                                  .execution_options(skip_tenant_filter=True))).scalars().all())


def _email() -> str:
    return f"Sam.{uuid.uuid4().hex[:6]}@Example.dev"


def test_the_unique_index_is_on_lower_email_with_no_shop_column():
    idx = {i.name: i for i in Employee.__table__.indexes}["uq_employees_email_lower"]
    assert idx.unique
    cols = [str(e) for e in idx.expressions]
    assert cols == ["lower(email)"], cols   # not (org_id, lower(email)): one email, one person in Tekly


async def test_the_database_refuses_one_email_in_two_shops(db):
    a = Organisation(name="A", plan="free", currency="$", currency_code="USD", timezone="UTC")
    b = Organisation(name="B", plan="free", currency="$", currency_code="USD", timezone="UTC")
    db.add_all([a, b])
    await db.flush()
    db.add(Employee(org_id=a.id, name="Sam", email="sam@example.dev", password_hash="x"))
    await db.flush()
    db.add(Employee(org_id=b.id, name="Sam", email="SAM@example.dev", password_hash="x"))
    with pytest.raises(IntegrityError):
        await db.flush()
    await db.rollback()


async def test_signup_staff_add_and_invite_never_make_a_second_person(client, db):
    email = _email()
    await _shop(client, email, "A")
    other = await _shop(client, _email(), "B")

    # a new shop signing up with the same address (any case) gets no second account
    await client.post("/api/auth/register", json={"org_name": "C", "name": "Sam", "email": email.upper(), "password": PW})
    # shop B adding or inviting that address is refused (never a 500)
    add = await client.post("/api/staff/employees", headers=other, json={"name": "Sam", "email": email.lower(), "password": PW})
    assert add.status_code in (400, 409), add.text
    inv = await client.post("/api/staff/invites", headers=other, json={"email": email.lower(), "name": "Sam"})
    assert inv.status_code in (400, 409, 422), inv.text
    assert len(await _people(db, email)) == 1


async def test_sign_in_and_reset_resolve_the_one_person(client, db):
    email = _email()
    await _shop(client, email, "A")
    await _shop(client, _email(), "B")
    (sam,) = await _people(db, email)
    sam_id, sam_org = sam.id, sam.org_id

    r = await client.post("/api/auth/token", data={"username": email.upper(), "password": PW})
    assert r.status_code == 200, r.text
    me = (await client.get("/api/auth/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"})).json()
    assert me["id"] == sam_id and me["org_id"] == sam_org

    f = await client.post("/api/auth/password/forgot", json={"email": email.lower()})
    assert f.status_code == 200
    db.expire_all()
    owners = (await db.execute(select(AuthToken.employee_id).where(AuthToken.kind == "reset")
                               .execution_options(skip_tenant_filter=True))).scalars().all()
    assert set(owners) == {sam_id}
