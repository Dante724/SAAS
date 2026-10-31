"""Audit 29 Oct 2026, the "Low (batch these)" security list (docs/audits/2026-10-29-full-codebase-audit.md).

One test (or a few) per item; each failed before its fix."""
import asyncio
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select

from backend.models import Employee, Organisation, Role
from backend.services.auth import create_access_token, hash_password


async def _me(client, h):
    return (await client.get("/api/auth/me", headers=h)).json()


async def _member(db, org_id, perms: dict, *, name="Tech", active=True):
    """A teammate on an explicit role that grants exactly `perms`; returns (employee, headers)."""
    role = Role(org_id=org_id, name=f"r-{uuid.uuid4().hex[:6]}", permissions=perms, perms_explicit=True)
    db.add(role)
    await db.flush()
    emp = Employee(org_id=org_id, role_id=role.id, name=name, email=f"m-{uuid.uuid4().hex[:8]}@test.dev",
                   password_hash=hash_password("pw-test-12345"), active=active)
    db.add(emp)
    await db.commit()
    tok = create_access_token({"sub": emp.id, "org_id": org_id, "tv": emp.token_version or 0})
    return emp, {"Authorization": f"Bearer {tok}"}


async def _client_id(client, h, name="Ana Lima"):
    return (await client.post("/api/clients", json={"name": name, "phone": "+15550199"}, headers=h)).json()["id"]


# ── 1. Customer portal links: hashed, expiring, permission-gated ──────────────────────────────
@pytest.mark.asyncio
async def test_portal_link_is_stored_hashed(client, tenant, db):
    from backend.models import ClientPortalLink
    cid = await _client_id(client, tenant)
    raw = (await client.post(f"/api/clients/{cid}/portal-link", headers=tenant)).json()["portal_token"]
    rows = (await db.execute(select(ClientPortalLink).where(ClientPortalLink.client_id == cid))).scalars().all()
    assert len(rows) == 1
    assert rows[0].token_hash != raw and raw not in rows[0].token_hash
    assert rows[0].expires_at > datetime.utcnow() + timedelta(days=29)
    assert (await client.get(f"/api/public/portal/{raw}")).status_code == 200


@pytest.mark.asyncio
async def test_portal_link_expires(client, tenant, db):
    from backend.models import ClientPortalLink
    cid = await _client_id(client, tenant)
    raw = (await client.post(f"/api/clients/{cid}/portal-link", headers=tenant)).json()["portal_token"]
    row = (await db.execute(select(ClientPortalLink).where(ClientPortalLink.client_id == cid))).scalar_one()
    row.expires_at = datetime.utcnow() - timedelta(minutes=1)
    await db.commit()
    assert (await client.get(f"/api/public/portal/{raw}")).status_code == 404


@pytest.mark.asyncio
async def test_reissuing_a_portal_link_stops_the_old_one(client, tenant):
    cid = await _client_id(client, tenant)
    old = (await client.post(f"/api/clients/{cid}/portal-link", headers=tenant)).json()["portal_token"]
    new = (await client.post(f"/api/clients/{cid}/portal-link", headers=tenant)).json()["portal_token"]
    assert old != new
    assert (await client.get(f"/api/public/portal/{old}")).status_code == 404
    assert (await client.get(f"/api/public/portal/{new}")).status_code == 200


@pytest.mark.asyncio
async def test_portal_link_needs_permission(client, tenant, db):
    me = await _me(client, tenant)
    cid = await _client_id(client, tenant)
    _, h = await _member(db, me["org_id"], {"can_create_tickets": True})
    r = await client.post(f"/api/clients/{cid}/portal-link", headers=h)
    assert r.status_code == 403
    _, h2 = await _member(db, me["org_id"], {"can_view_client_info": True})
    assert (await client.post(f"/api/clients/{cid}/portal-link", headers=h2)).status_code == 200


# ── 2. Account enumeration ────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_register_with_a_taken_email_does_not_say_so(client, tenant, monkeypatch):
    sent = []

    async def fake_send(to, subject, body, *a, **kw):
        sent.append(to)
    from backend.routers import auth as auth_router
    monkeypatch.setattr(auth_router, "send_direct_email", fake_send)
    email = (await _me(client, tenant))["email"]
    r = await client.post("/api/auth/register", json={
        "org_name": "X", "name": "Y", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 400
    assert "registered" not in r.text.lower() and "exists" not in r.text.lower()
    # the real owner of the address is told instead
    await asyncio.gather(*auth_router._BACKGROUND)
    assert sent == [email]


@pytest.mark.asyncio
async def test_forgot_password_sends_the_email_after_answering(client, tenant, monkeypatch):
    """The reply mustn't wait for SMTP only when the address has an account (timing leak)."""
    from backend.routers import auth as auth_router
    started = asyncio.Event()

    async def slow_send(to, subject, body, *a, **kw):
        started.set()
        await asyncio.sleep(5)
    monkeypatch.setattr(auth_router, "send_direct_email", slow_send)
    email = (await _me(client, tenant))["email"]
    r = await asyncio.wait_for(client.post("/api/auth/password/forgot", json={"email": email}), timeout=3)
    assert r.status_code == 200
    await asyncio.wait_for(started.wait(), timeout=3)   # ...and the email still goes
    for t in list(auth_router._BACKGROUND):
        t.cancel()
    r2 = await client.post("/api/auth/password/forgot", json={"email": "nobody-here@test.dev"})
    assert r.json() == r2.json()


@pytest.mark.asyncio
async def test_disabled_and_suspended_accounts_get_one_message(client, tenant, db):
    me = await _me(client, tenant)
    emp, _ = await _member(db, me["org_id"], {}, active=False)
    off = await client.post("/api/auth/token", data={"username": emp.email, "password": "pw-test-12345"})
    org = await db.get(Organisation, me["org_id"])
    org.sub_status = "suspended"
    await db.commit()
    susp = await client.post("/api/auth/token", data={"username": me["email"], "password": "pw-test-12345"})
    assert off.status_code == susp.status_code == 403
    assert off.json()["detail"] == susp.json()["detail"]


# ── 3. Reset tokens are single-use under concurrency ──────────────────────────────────────────
@pytest.mark.asyncio
async def test_reset_token_redeems_once_when_two_requests_race(client, tenant, db):
    """Both requests read the link as unused; only one may redeem it."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from backend.models import AuthToken
    from backend.services import tokens
    TestSession = async_sessionmaker(db.bind, expire_on_commit=False)
    me = await _me(client, tenant)
    raw = await tokens.issue(db, employee_id=me["id"], kind="reset")
    await db.commit()
    async with TestSession() as a, TestSession() as b:
        held = (await b.execute(select(AuthToken))).scalars().all()   # b has read it as unused
        first = await tokens.redeem(a, raw=raw, kind="reset")
        await a.commit()
        second = await tokens.redeem(b, raw=raw, kind="reset")
        await b.commit()
    assert first is not None and first.id == me["id"]
    assert second is None and held


# ── 4. API keys ───────────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_api_key_stops_when_its_creator_is_deactivated(client, tenant, db):
    me = await _me(client, tenant)
    emp, h = await _member(db, me["org_id"], {"can_manage_settings": True})
    key = (await client.post("/api/api-keys", json={"name": "zap", "scopes": "read"}, headers=h)).json()["key"]
    kh = {"Authorization": f"Bearer {key}"}
    assert (await client.get("/api/v1/contacts", headers=kh)).status_code == 200
    emp = await db.get(Employee, emp.id)
    emp.active = False
    await db.commit()
    assert (await client.get("/api/v1/contacts", headers=kh)).status_code == 401


@pytest.mark.asyncio
async def test_listing_api_keys_needs_permission(client, tenant, db):
    me = await _me(client, tenant)
    _, h = await _member(db, me["org_id"], {"can_create_tickets": True})
    assert (await client.get("/api/api-keys", headers=h)).status_code == 403
    assert (await client.get("/api/api-keys", headers=tenant)).status_code == 200


# ── 5. Payment links are capped at what's owed, in the document's currency ───────────────────
async def _order_80(client, h):
    loc = (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]
    cid = await _client_id(client, h)
    o = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc}, headers=h)).json()
    await client.post(f"/api/orders/{o['id']}/lines",
                      json={"line_type": "labour", "description": "Screen", "qty": 1, "unit_price": 80}, headers=h)
    return o["id"]


@pytest.mark.asyncio
async def test_pay_link_with_explicit_currency_is_still_capped(client, tenant):
    oid = await _order_80(client, tenant)
    base = (await _me(client, tenant)).get("currency_code") or "USD"
    r = await client.post("/api/payments/checkout", json={"order_id": oid, "amount": 500, "currency": base},
                          headers=tenant)
    assert r.status_code == 422, r.text
    ok = await client.post("/api/payments/checkout", json={"order_id": oid, "amount": 80, "currency": base},
                           headers=tenant)
    assert ok.status_code == 201, ok.text


@pytest.mark.asyncio
async def test_pay_link_refuses_a_foreign_currency_for_an_order(client, tenant):
    oid = await _order_80(client, tenant)
    base = (await _me(client, tenant)).get("currency_code") or "USD"
    other = "EUR" if base != "EUR" else "GBP"
    r = await client.post("/api/payments/checkout", json={"order_id": oid, "amount": 10, "currency": other},
                          headers=tenant)
    assert r.status_code == 422, r.text


# ── 6. Unknown or another shop's cash account ────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_cash_entry_into_unknown_account_is_404(client, tenant):
    r = await client.post("/api/cash/transactions", json={
        "account_id": "no-such-account", "txn_type": "income", "amount": 10}, headers=tenant)
    assert r.status_code == 404, r.text


@pytest.mark.asyncio
async def test_cash_entry_into_another_shops_account_is_404(client, tenant, db):
    other = await client.post("/api/auth/register", json={
        "org_name": "Other", "name": "O", "email": f"o-{uuid.uuid4().hex[:6]}@test.dev", "password": "pw-test-12345"})
    oh = {"Authorization": f"Bearer {other.json()['access_token']}"}
    await client.post("/api/cash/accounts", json={"name": "Their till", "payment_option": "cash"}, headers=oh)
    theirs = [a for a in (await client.get("/api/cash/accounts", headers=oh)).json()["items"]
              if a["name"] == "Their till"][0]["id"]
    r = await client.post("/api/cash/transactions", json={
        "account_id": theirs, "txn_type": "income", "amount": 10}, headers=tenant)
    assert r.status_code == 404, r.text
    from backend.models import Transaction
    assert (await db.execute(select(func.count()).select_from(Transaction).where(
        Transaction.account_id == theirs))).scalar() == 0


# ── 8. HTML emails ────────────────────────────────────────────────────────────────────────────
def test_client_values_are_escaped_in_html_templates():
    from backend.services import message_vars as mv
    vals = {"client_name": '<img src=x onerror="alert(1)">'}
    html = mv.apply("<p>Hi {client_name}</p>", vals)
    assert "<img" not in html and "&lt;img" in html
    # a plain-text template keeps the value as typed (it's sent as text/plain)
    assert mv.apply("Hi {client_name}", vals) == 'Hi <img src=x onerror="alert(1)">'
    # document values that are deliberate markup stay markup; other document values are escaped
    from backend.routers.documents import _Html
    out = mv.apply("<p>{Lines}{Note}</p>", {}, {"{Lines}": _Html("<table></table>"), "{Note}": "<b>x</b>"})
    assert "<table></table>" in out and "&lt;b&gt;x" in out


def test_html_or_plain_is_decided_by_the_template():
    from backend.services import message_vars as mv
    assert mv.is_html("<p>Hello {client_name}</p>")
    assert not mv.is_html("Hello {client_name}")


@pytest.mark.asyncio
async def test_smtp_sends_plain_template_as_plain_text_and_strips_subject_newlines(monkeypatch):
    from backend.services import notifications as n
    sent = {}

    class FakeSMTP:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self):
            pass

        def login(self, *a):
            pass

        def send_message(self, msg):
            sent["msg"] = msg
    import smtplib
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    ad = n.SmtpEmailAdapter({"host": "smtp.test", "from": "shop@test.dev"})
    r = await ad.send(recipient="c@test.dev", subject="Ready\r\nBcc: victim@evil.test",
                      body="Hi <b>Bo</b>", html=False)
    assert r["status"] == "sent"
    msg = sent["msg"]
    assert msg.get_content_subtype() == "plain"
    assert "\n" not in msg["Subject"] and "\r" not in msg["Subject"]
    assert msg["Bcc"] is None


@pytest.mark.asyncio
async def test_rule_send_uses_the_templates_format(client, tenant, db, monkeypatch):
    from backend.models import NotificationTemplate
    from backend.services import notification_rules as nr
    me = await _me(client, tenant)
    seen = []

    class Rec:
        async def send(self, *, recipient, subject, body, html=None):
            seen.append(html)
            return {"status": "sent", "error": None}
    monkeypatch.setattr(nr, "_adapter_for", lambda ch, creds=None: Rec())
    t = NotificationTemplate(org_id=me["org_id"], name="t", channel="email", trigger_event="status_change",
                             subject="s", body_template="Hi {client_name}")
    db.add(t)
    await db.commit()
    await nr._send(db, me["org_id"], "email", recipient="a@b.c", subject="s", body="Hi <b>x</b>", template_id=t.id)
    await nr._send(db, me["org_id"], "email", recipient="a@b.c", subject="s", body="Hi <b>x</b>")
    assert seen == [False, False]


# ── 9. LIKE wildcards are literal in search; brand/model dedupe is exact ─────────────────────
@pytest.mark.asyncio
async def test_percent_in_catalog_search_is_literal(client, tenant):
    for name in ("50% off fit", "Screen fit"):
        assert (await client.post("/api/catalog/services", json={"name": name, "price": 1},
                                  headers=tenant)).status_code == 201
    names = [s["name"] for s in (await client.get("/api/catalog/services", params={"search": "%"},
                                                  headers=tenant)).json()["items"]]
    assert names == ["50% off fit"]
    got = (await client.get("/api/catalog/services", params={"search": "_"}, headers=tenant)).json()["items"]
    assert got == []


@pytest.mark.asyncio
async def test_percent_in_product_and_lead_search_is_literal(client, tenant):
    for name in ("Glass 100%", "Battery"):
        await client.post("/api/catalog/products", json={"name": name}, headers=tenant)
    prods = (await client.get("/api/catalog/products", params={"search": "%"}, headers=tenant)).json()
    items = prods["items"] if isinstance(prods, dict) else prods
    assert [p["name"] for p in items] == ["Glass 100%"]
    for name in ("Ana_Lima", "Bo Chen"):
        assert (await client.post("/api/leads", json={"name": name, "phone": "+15550123"},
                                  headers=tenant)).status_code == 201
    leads = (await client.get("/api/leads", params={"q": "_"}, headers=tenant)).json()["items"]
    assert [x["name"] for x in leads] == ["Ana_Lima"]


@pytest.mark.asyncio
async def test_custom_report_contains_is_literal(client, tenant, db):
    from backend.models import Client
    from backend.services.custom_reports import _clause
    me = await _me(client, tenant)
    for name in ("50% Club", "500 Club"):
        db.add(Client(org_id=me["org_id"], name=name))
    await db.commit()
    got = (await db.execute(select(Client.name).where(
        Client.org_id == me["org_id"], _clause(Client.name, "contains", "50%")))).scalars().all()
    assert got == ["50% Club"]


@pytest.mark.asyncio
async def test_brand_named_percent_does_not_match_every_brand(client, tenant):
    h = tenant
    r = await client.post("/api/settings/asset-brands", json={"template_key": "mobile_phone", "name": "Apple"}, headers=h)
    assert r.status_code == 201, r.text
    await client.post("/api/settings/asset-brands", json={"template_key": "mobile_phone", "name": "Acer"}, headers=h)
    r = await client.post("/api/settings/asset-brands", json={"template_key": "mobile_phone", "name": "%"}, headers=h)
    assert r.status_code == 201, r.text
    # still a duplicate when only the case differs
    r = await client.post("/api/settings/asset-brands", json={"template_key": "mobile_phone", "name": "apple"}, headers=h)
    assert r.status_code == 409


# ── 11. Rate limits ───────────────────────────────────────────────────────────────────────────
def _strong(**kw):
    from cryptography.fernet import Fernet
    return dict(ENVIRONMENT="production", SECRET_KEY="x" * 48, CORS_ORIGINS="https://app.tekly.test",
                DATABASE_URL="postgresql+asyncpg://u:p@db/tekly", PUBLIC_BASE_URL="https://app.tekly.test",
                SMTP_HOST="smtp.test", SMTP_FROM="no-reply@tekly.test",
                TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode(), **kw)


def test_deploy_with_several_workers_needs_redis(monkeypatch):
    from backend.config import Settings
    monkeypatch.setenv("WEB_CONCURRENCY", "4")
    with pytest.raises(ValueError, match="REDIS_URL"):
        Settings(**_strong())
    Settings(**_strong(REDIS_URL="redis://cache:6379/0"))
    monkeypatch.setenv("WEB_CONCURRENCY", "1")
    Settings(**_strong())


def test_short_forwarded_chain_uses_the_peer(monkeypatch):
    from types import SimpleNamespace

    from backend.config import settings
    from backend.services import ratelimit
    monkeypatch.setattr(settings, "TRUSTED_PROXY_HOPS", 2)
    req = SimpleNamespace(client=SimpleNamespace(host="10.0.0.9"), headers={"x-forwarded-for": "6.6.6.6"})
    assert ratelimit.client_ip(req) == "10.0.0.9"


# ── 13. Headers ───────────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_health_hides_environment_and_csp_limits_forms(client):
    r = await client.get("/api/health")
    assert "environment" not in r.json()
    assert "form-action 'self'" in r.headers["content-security-policy"]


# ── 14. Retention ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_retention_keeps_everything_by_default_and_purges_when_set(client, tenant, db):
    from backend.models import AuditLog, Call, NotificationLog, StaffNotification
    from backend.services import retention
    me = await _me(client, tenant)
    old = datetime.utcnow() - timedelta(days=400)
    db.add_all([
        NotificationLog(org_id=me["org_id"], channel="email", trigger_event="x", status="sent", created_at=old),
        NotificationLog(org_id=me["org_id"], channel="email", trigger_event="x", status="scheduled", created_at=old),
        NotificationLog(org_id=me["org_id"], channel="email", trigger_event="x", status="sent"),
        StaffNotification(org_id=me["org_id"], employee_id=me["id"], kind="k", title="t", created_at=old),
        Call(org_id=me["org_id"], transcript="hello", created_at=old),
        AuditLog(org_id=me["org_id"], action="x", entity_type="x", created_at=old),
    ])
    await db.commit()

    async def count(model):
        return (await db.execute(select(func.count()).select_from(model).where(model.org_id == me["org_id"]))).scalar()
    await retention.purge_all(db)
    assert await count(NotificationLog) == 3 and await count(Call) == 1

    r = await client.get("/api/compliance/retention", headers=tenant)
    assert r.status_code == 200 and all(k["days"] is None for k in r.json()["kinds"])
    assert (await client.put("/api/compliance/retention", json={"days": {"calls": 5}},
                             headers=tenant)).status_code == 422
    r = await client.put("/api/compliance/retention", json={"days": {"notifications": 365, "calls": 365}},
                         headers=tenant)
    assert r.status_code == 200, r.text
    await retention.purge_all(db)
    db.expire_all()
    assert await count(NotificationLog) == 2          # the new one and the still-scheduled one
    assert await count(StaffNotification) == 0 and await count(Call) == 0
    assert await count(AuditLog) >= 1                 # audit wasn't chosen


@pytest.mark.asyncio
async def test_retention_settings_need_permission(client, tenant, db):
    me = await _me(client, tenant)
    _, h = await _member(db, me["org_id"], {"can_create_tickets": True})
    assert (await client.put("/api/compliance/retention", json={"days": {}}, headers=h)).status_code == 403


def test_daily_retention_job_is_registered():
    from backend.core import scheduled  # noqa: F401
    from backend.core.jobs import job_runner
    assert "retention-purge" in {p.name for p in job_runner._periodics}


# ── 15. Passcodes in custom fields are encrypted at rest ─────────────────────────────────────
@pytest.mark.asyncio
async def test_passcode_custom_field_is_encrypted_at_rest(client, tenant, db, monkeypatch):
    from cryptography.fernet import Fernet
    from sqlalchemy import text

    from backend.config import settings
    from backend.models import Order
    monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    me = await _me(client, tenant)
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    o = Order(org_id=me["org_id"], location_id=loc, order_number="T-1",
              custom_data={"passcode": "4321", "pattern_lock": 1478, "accessories": "Case"})
    db.add(o)
    await db.commit()
    oid = o.id
    raw = (await db.execute(text("SELECT custom_data FROM orders WHERE id = :i"), {"i": oid})).scalar()
    raw = raw if isinstance(raw, str) else __import__("json").dumps(raw)
    assert "4321" not in raw and "1478" not in raw and "Case" in raw
    db.expire_all()
    back = await db.get(Order, oid)
    assert back.custom_data == {"passcode": "4321", "pattern_lock": 1478, "accessories": "Case"}
