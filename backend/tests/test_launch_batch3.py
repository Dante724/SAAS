"""Launch fixes, batch 3: staff still on the old default password get no session and are sent a
reset link (H1); settling an order deletes an open Square pay link at Square (B3)."""
import uuid

import pytest
from sqlalchemy import select

from backend.models import Employee, Organisation, PaymentIntent
from backend.services.auth import hash_password
from backend.tests.test_money_hardening import _account, _order_with_total


async def _login(client, email, pw):
    return await client.post("/api/auth/token", data={"username": email, "password": pw})


@pytest.fixture
def outbox(monkeypatch):
    sent = []

    async def fake_send(to, subject, body, *a, **kw):
        sent.append({"to": to, "body": body})
    from backend.routers import auth as auth_router
    monkeypatch.setattr(auth_router, "send_direct_email", fake_send)
    return sent


async def _staff(db, password, **kw):
    org = (await db.execute(select(Organisation))).scalars().first()
    email = f"old-{uuid.uuid4().hex[:6]}@default.test"
    emp = Employee(org_id=org.id, name="Old", email=email, password_hash=hash_password(password), active=True, **kw)
    db.add(emp)
    await db.commit()
    return emp


@pytest.mark.asyncio
async def test_old_default_password_gets_a_reset_link_not_a_session(client, tenant, db, outbox):
    emp = await _staff(db, "changeme123")
    r = await _login(client, emp.email, "changeme123")
    assert r.status_code == 403 and "access_token" not in r.text
    assert r.headers.get("X-Password-Reset-Required") == "1"
    assert len(outbox) == 1 and outbox[0]["to"] == emp.email
    await db.refresh(emp)
    assert emp.must_reset_password is True
    # the old default can't be chosen again, a new password works and clears the flag
    token = outbox[0]["body"].split("token=")[1].split()[0]
    assert (await client.post("/api/auth/password/reset", json={"token": token, "new_password": "changeme123"})).status_code == 400
    assert (await client.post("/api/auth/password/reset", json={"token": token, "new_password": "a-new-pass-123"})).status_code == 200
    await db.refresh(emp)
    assert emp.must_reset_password is False
    assert (await _login(client, emp.email, "a-new-pass-123")).status_code == 200


@pytest.mark.asyncio
async def test_flagged_account_is_held_even_with_another_password(client, tenant, db, outbox):
    emp = await _staff(db, "someone-elses-123", must_reset_password=True)
    assert (await _login(client, emp.email, "someone-elses-123")).status_code == 403
    # repeated attempts don't flood the inbox
    for _ in range(5):
        await _login(client, emp.email, "someone-elses-123")
    assert len(outbox) <= 3
    # a wrong password still just says wrong password (no hint the account exists)
    assert (await _login(client, emp.email, "wrong-password")).status_code == 401


@pytest.mark.asyncio
async def test_admin_cannot_set_the_old_default_and_a_real_password_clears_the_flag(client, tenant, db, outbox):
    emp = await _staff(db, "changeme123", must_reset_password=True)
    url = f"/api/staff/employees/{emp.id}/password"
    assert (await client.post(url, headers=tenant, json={"password": "changeme123"})).status_code == 400
    r = await client.post(url, headers=tenant, json={"password": "set-by-admin-123"})
    assert r.status_code == 200, r.text
    await db.refresh(emp)
    assert emp.must_reset_password is False
    assert (await _login(client, emp.email, "set-by-admin-123")).status_code == 200


@pytest.mark.asyncio
async def test_settling_an_order_deletes_the_square_link(client, tenant, db, monkeypatch):
    _cid, oid = await _order_with_total(client, tenant, 100)
    aid = await _account(client, tenant)
    link = (await client.post(f"/api/orders/{oid}/payment-link", headers=tenant, json={})).json()
    intent = (await db.execute(select(PaymentIntent).where(PaymentIntent.id == link["intent_id"]))).scalar_one()
    intent.provider, intent.provider_ref, intent.provider_link_id = "square", "sq_order_1", "sq_link_9"
    await db.commit()
    from backend.config import settings
    monkeypatch.setattr(settings, "SQUARE_ACCESS_TOKEN", "sq-test-token")
    calls = []

    class _Resp:
        status_code = 200
    import httpx

    async def fake_delete(self, url, *a, **kw):
        calls.append(str(url))
        return _Resp()
    monkeypatch.setattr(httpx.AsyncClient, "delete", fake_delete)
    await client.post(f"/api/cash/orders/{oid}/pay", headers=tenant,
                      json={"method": "cash", "account_id": aid, "amount": 100})
    await db.refresh(intent)
    assert intent.status == "canceled"
    assert len(calls) == 1 and calls[0].endswith("/v2/online-checkout/payment-links/sq_link_9")
