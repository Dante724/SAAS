"""Sign-in hardening: the two-step MFA login the UI uses, a real logout route, and sign-in /
MFA / password-reset events landing in the audit log."""
import uuid

import pytest
from sqlalchemy import select

from backend.models import AuditLog, Employee
from backend.services import mfa, ratelimit

PW = "pw-test-12345"


@pytest.fixture(autouse=True)
def _fresh_limits(monkeypatch):
    monkeypatch.setattr(ratelimit, "_redis", False)
    ratelimit._HITS.clear()
    yield
    ratelimit._HITS.clear()


async def _register(client):
    email = f"owner-{uuid.uuid4().hex[:8]}@test.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Audit Co", "name": "Owner", "email": email, "password": PW})
    assert r.status_code == 201, r.text
    return email, {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _login(client, email, pw=PW, code=None):
    data = {"username": email, "password": pw}
    if code is not None:
        data["client_secret"] = code
    return await client.post("/api/auth/token", data=data)


async def _actions(db, email):
    emp = (await db.execute(select(Employee).where(Employee.email == email))).scalar_one()
    rows = (await db.execute(select(AuditLog).where(AuditLog.entity_id == emp.id)
                             .order_by(AuditLog.created_at))).scalars().all()
    return [r.action for r in rows], rows


async def _enable_mfa(client, headers):
    secret = (await client.post("/api/mfa/enroll", json={}, headers=headers)).json()["secret"]
    r = await client.post("/api/mfa/activate", json={"code": mfa.totp(secret)}, headers=headers)
    assert r.status_code == 200
    return secret, r.json()["recovery_codes"]


@pytest.mark.asyncio
async def test_mfa_two_step_login_via_client_secret(client, db, monkeypatch):
    email, headers = await _register(client)
    secret, recovery = await _enable_mfa(client, headers)

    # Step 1: the right password alone asks for the code.
    first = await _login(client, email)
    assert first.status_code == 401 and first.headers.get("x-mfa-required") == "1"
    # A wrong code is refused the same way.
    wrong = await _login(client, email, code="000000")
    assert wrong.status_code == 401 and wrong.headers.get("x-mfa-required") == "1"
    # Step 2: password + the next TOTP code in client_secret signs in. (The code used to
    # activate MFA is spent, so move one time step on.)
    import time as _t
    later = _t.time() + 30
    monkeypatch.setattr(mfa.time, "time", lambda: later)
    ok = await _login(client, email, code=mfa.totp(secret, at=int(later)))
    assert ok.status_code == 200 and ok.json()["access_token"]
    # A recovery code also works, once.
    assert (await _login(client, email, code=recovery[0])).status_code == 200
    assert (await _login(client, email, code=recovery[0])).status_code == 401


@pytest.mark.asyncio
async def test_logout_route(client, db):
    email, headers = await _register(client)
    r = await client.post("/api/auth/logout", headers=headers)
    assert r.status_code == 204
    assert (await client.post("/api/auth/logout")).status_code == 401
    actions, _ = await _actions(db, email)
    assert "auth.logout" in actions


@pytest.mark.asyncio
async def test_signin_events_are_audited(client, db):
    email, headers = await _register(client)
    assert (await _login(client, email, pw="not-the-password")).status_code == 401
    assert (await _login(client, email)).status_code == 200
    actions, rows = await _actions(db, email)
    assert actions.count("auth.login_failed") == 1 and actions.count("auth.login") == 1
    failed = next(r for r in rows if r.action == "auth.login_failed")
    assert failed.diff == {"reason": "password"} and failed.org_id

    # Unknown emails are refused without writing anything (there is no org to file it under).
    before = len((await db.execute(select(AuditLog))).scalars().all())
    assert (await _login(client, f"nobody-{uuid.uuid4().hex[:6]}@test.dev")).status_code == 401
    db.expire_all()
    assert len((await db.execute(select(AuditLog))).scalars().all()) == before


@pytest.mark.asyncio
async def test_mfa_enable_disable_and_wrong_code_are_audited(client, db):
    email, headers = await _register(client)
    await _enable_mfa(client, headers)
    assert (await _login(client, email)).status_code == 401           # no code: not a failure yet
    assert (await _login(client, email, code="000000")).status_code == 401
    assert (await client.post("/api/mfa/disable", json={"password": PW}, headers=headers)).status_code == 200
    actions, rows = await _actions(db, email)
    assert "mfa.enable" in actions and "mfa.disable" in actions
    fails = [r.diff for r in rows if r.action == "auth.login_failed"]
    assert fails == [{"reason": "mfa_code"}]


@pytest.mark.asyncio
async def test_password_reset_is_audited(client, db, monkeypatch):
    email, _ = await _register(client)
    from backend.services import tokens as _tok
    raws = []
    real_issue = _tok.issue

    async def _capture(*a, **kw):
        raw = await real_issue(*a, **kw)
        raws.append(raw)
        return raw
    monkeypatch.setattr(_tok, "issue", _capture)
    assert (await client.post("/api/auth/password/forgot", json={"email": email})).status_code == 200
    r = await client.post("/api/auth/password/reset", json={"token": raws[-1], "new_password": "brand-new-pw-1"})
    assert r.status_code == 200, r.text
    actions, _ = await _actions(db, email)
    assert "auth.password_reset_requested" in actions and "auth.password_reset" in actions
