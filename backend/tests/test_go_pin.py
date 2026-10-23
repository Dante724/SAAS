"""Tekly Go payment PIN: set needs the password, only a hash is stored, wrong PINs are capped.

- POST /api/auth/pin sets/changes the PIN (4–6 digits, not 1111 / 1234) and needs the password
- the stored value is a hash, never the PIN
- POST /api/auth/pin/verify says yes/no; the 5th wrong PIN in 15 minutes signs the person out
  everywhere (their token stops working)
- it's per person: one employee's PIN doesn't unlock another's
- GET /api/auth/pin says whether the person needs one (can take payments or make pay links)
"""
import pytest
from sqlalchemy import select

from backend.models import Employee
from backend.routers.auth import pin_problem
from backend.services import ratelimit
from backend.tests.test_sensitive_reads import _restricted

PW = "pw-test-12345"   # the conftest tenant's password


@pytest.fixture(autouse=True)
def fresh_limits(monkeypatch):
    monkeypatch.setattr(ratelimit, "_redis", False)
    monkeypatch.setattr(ratelimit, "_aredis", False)
    monkeypatch.setattr(ratelimit, "_aredis_retry_at", 1e18)
    ratelimit._HITS.clear()
    yield
    ratelimit._HITS.clear()


@pytest.mark.parametrize("pin, ok", [
    ("2580", True), ("739104", True), ("1357", True),
    ("123", False), ("1234567", False), ("12a4", False), ("", False),
    ("1111", False), ("1234", False), ("9876", False), ("456789", False),
])
def test_pin_rules(pin, ok):
    assert (pin_problem(pin) is None) == ok


@pytest.mark.asyncio
async def test_set_needs_password_and_stores_only_a_hash(client, db, tenant):
    assert (await client.get("/api/auth/pin", headers=tenant)).json() == {"set": False, "required": True}
    r = await client.post("/api/auth/pin", headers=tenant, json={"pin": "2580", "password": "nope"})
    assert r.status_code == 403
    r = await client.post("/api/auth/pin", headers=tenant, json={"pin": "1234", "password": PW})
    assert r.status_code == 400 and "run" in r.json()["detail"]
    r = await client.post("/api/auth/pin", headers=tenant, json={"pin": "2580", "password": PW})
    assert r.status_code == 200, r.text
    assert (await client.get("/api/auth/pin", headers=tenant)).json()["set"] is True
    assert (await client.get("/api/auth/me", headers=tenant)).json()["pin_set"] is True
    emp = (await db.execute(select(Employee).where(Employee.pin_hash.is_not(None)))).scalars().one()
    assert "2580" not in emp.pin_hash and emp.pin_hash.startswith("$2")


@pytest.mark.asyncio
async def test_verify_right_and_wrong_and_change(client, tenant):
    assert (await client.post("/api/auth/pin/verify", headers=tenant, json={"pin": "2580"})).status_code == 409
    await client.post("/api/auth/pin", headers=tenant, json={"pin": "2580", "password": PW})
    assert (await client.post("/api/auth/pin/verify", headers=tenant, json={"pin": "2580"})).json() == {"ok": True}
    r = await client.post("/api/auth/pin/verify", headers=tenant, json={"pin": "0000"})
    assert r.status_code == 403 and r.json()["detail"] == "Wrong PIN"
    # changing it: the old one stops working
    await client.post("/api/auth/pin", headers=tenant, json={"pin": "739104", "password": PW})
    assert (await client.post("/api/auth/pin/verify", headers=tenant, json={"pin": "2580"})).status_code == 403
    assert (await client.post("/api/auth/pin/verify", headers=tenant, json={"pin": "739104"})).status_code == 200


@pytest.mark.asyncio
async def test_fifth_wrong_pin_signs_out_everywhere(client, tenant):
    await client.post("/api/auth/pin", headers=tenant, json={"pin": "2580", "password": PW})
    codes = [(await client.post("/api/auth/pin/verify", headers=tenant, json={"pin": f"{i:04d}"})).status_code
             for i in range(1, 5)]
    assert codes == [403] * 4
    assert (await client.get("/api/auth/me", headers=tenant)).status_code == 200   # still signed in
    r = await client.post("/api/auth/pin/verify", headers=tenant, json={"pin": "0005"})
    assert r.status_code == 429 and r.headers.get("x-signed-out") == "1"
    assert (await client.get("/api/auth/me", headers=tenant)).status_code == 401   # token revoked
    # the right PIN can't be used to get back in: the session is gone
    assert (await client.post("/api/auth/pin/verify", headers=tenant, json={"pin": "2580"})).status_code == 401


@pytest.mark.asyncio
async def test_setting_is_rate_limited(client, tenant):
    codes = [(await client.post("/api/auth/pin", headers=tenant, json={"pin": "2580", "password": "guess"})).status_code
             for _ in range(6)]
    assert codes[:5] == [403] * 5 and codes[5] == 429


@pytest.mark.asyncio
async def test_pin_is_per_person(client, db, tenant):
    await client.post("/api/auth/pin", headers=tenant, json={"pin": "2580", "password": PW})
    h, _, _ = await _restricted(client, db, [])
    assert (await client.post("/api/auth/pin/verify", headers=h, json={"pin": "2580"})).status_code == 409
    await client.post("/api/auth/pin", headers=h, json={"pin": "1357", "password": "pw-r-123456"})
    assert (await client.post("/api/auth/pin/verify", headers=h, json={"pin": "2580"})).status_code == 403
    assert (await client.post("/api/auth/pin/verify", headers=tenant, json={"pin": "1357"})).status_code == 403


@pytest.mark.asyncio
async def test_required_only_for_people_who_handle_money(client, db, tenant):
    h, _, _ = await _restricted(client, db, ["can_take_payments", "can_create_payment_links"])
    assert (await client.get("/api/auth/pin", headers=h)).json()["required"] is False
    h2, _, _ = await _restricted(client, db, ["can_create_payment_links"])
    assert (await client.get("/api/auth/pin", headers=h2)).json()["required"] is True
