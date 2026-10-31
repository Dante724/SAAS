"""Security: new employees must never get a known default password.

Regression for the audit finding where EmployeeIn.password defaulted to "changeme123" and the
React Settings form never sent a password, so every teammate created there could be signed into
by anyone who knew their email.
"""
import pytest
from sqlalchemy import select

from backend.models import AuthToken, Employee, Organisation
from backend.services.auth import hash_password


async def _login(client, email, pw):
    return await client.post("/api/auth/token", data={"username": email, "password": pw})


@pytest.fixture
def outbox(monkeypatch):
    sent = []

    async def fake_send(to, subject, body, *a, **kw):
        sent.append({"to": to, "subject": subject, "body": body})

    from backend.services import notifications
    monkeypatch.setattr(notifications, "send_direct_email", fake_send)
    return sent


@pytest.mark.asyncio
async def test_employee_without_password_gets_set_password_email_not_default(client, tenant, db, outbox):
    r = await client.post("/api/staff/employees", headers=tenant,
                          json={"name": "Nina", "email": "nina@nopw.test"})
    assert r.status_code == 201 and r.json()["invite_sent"] is True

    assert (await _login(client, "nina@nopw.test", "changeme123")).status_code != 200

    assert len(outbox) == 1 and outbox[0]["to"] == "nina@nopw.test"
    token = outbox[0]["body"].split("token=")[1].split()[0]

    # The link stays valid for days (a welcome email), not the 1-hour reset window.
    emp = (await db.execute(select(Employee).where(Employee.email == "nina@nopw.test"))).scalar_one()
    tok = (await db.execute(select(AuthToken).where(AuthToken.employee_id == emp.id))).scalar_one()
    assert (tok.expires_at - tok.created_at).days >= 6

    ok = await client.post("/api/auth/password/reset", json={"token": token, "new_password": "my-own-pass-1"})
    assert ok.status_code == 200
    assert (await _login(client, "nina@nopw.test", "my-own-pass-1")).status_code == 200


@pytest.mark.asyncio
async def test_explicit_password_still_works_and_sends_nothing(client, tenant, outbox):
    r = await client.post("/api/staff/employees", headers=tenant,
                          json={"name": "Omar", "email": "omar@pw.test", "password": "pw-omar-12345"})
    assert r.status_code == 201 and r.json()["invite_sent"] is False
    assert outbox == []
    assert (await _login(client, "omar@pw.test", "pw-omar-12345")).status_code == 200


@pytest.mark.asyncio
async def test_short_password_rejected(client, tenant, outbox):
    r = await client.post("/api/staff/employees", headers=tenant,
                          json={"name": "Pia", "email": "pia@short.test", "password": "1234567"})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_find_default_passwords_script_reports_exposed_accounts(client, tenant, db, capsys):
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from scripts import find_default_passwords

    org = (await db.execute(select(Organisation))).scalars().first()
    db.add(Employee(org_id=org.id, name="Old", email="old@default.test",
                    password_hash=hash_password("changeme123"), active=True))
    await db.commit()

    assert await find_default_passwords.main() == 1
    out = capsys.readouterr().out
    assert "old@default.test" in out
