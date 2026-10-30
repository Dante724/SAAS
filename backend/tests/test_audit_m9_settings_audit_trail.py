"""Audit 29 Oct 2026, M9: changing payment/SMS keys, webhooks, SSO or email accounts, and
platform-admin changes to a shop, left no audit record. Each now records who changed what —
field names only, never the values."""
import json

import pytest
from sqlalchemy import select

from backend.config import settings
from backend.models import AuditLog, EmailConnection

SECRET = "sk_test_SUPERSECRETVALUE123"


async def _me(client, h):
    return (await client.get("/api/auth/me", headers=h)).json()


async def _rows(db, org_id, action_prefix):
    db.expire_all()
    rows = (await db.execute(select(AuditLog).where(
        AuditLog.org_id == org_id, AuditLog.action.like(f"{action_prefix}%")))).scalars().all()
    return rows


def _no_values(rows):
    blob = json.dumps([r.diff for r in rows], default=str)
    assert SECRET not in blob and "hooks.example.com" not in blob and "idp-secret" not in blob


@pytest.mark.asyncio
async def test_credential_changes_are_recorded_without_values(client, tenant, db):
    me = await _me(client, tenant)
    r = await client.put("/api/integrations/credentials", headers=tenant, json={
        "provider": "stripe", "data": {"secret_key": SECRET, "success_url": "https://shop.example.com/ok"}})
    assert r.status_code == 200, r.text
    r = await client.delete("/api/integrations/credentials/stripe", headers=tenant)
    assert r.status_code == 200, r.text
    rows = await _rows(db, me["org_id"], "integration.credentials")
    actions = sorted(r.action for r in rows)
    assert actions == ["integration.credentials.delete", "integration.credentials.update"]
    upd = next(r for r in rows if r.action.endswith("update"))
    assert upd.actor_id == me["id"] and upd.entity_id == "stripe"
    assert sorted(upd.diff["fields"]) == ["secret_key", "success_url"]
    _no_values(rows)


@pytest.mark.asyncio
async def test_webhook_changes_are_recorded(client, tenant, db, monkeypatch):
    from backend.core import ssrf
    monkeypatch.setattr(ssrf, "assert_safe_url", lambda url: None)
    me = await _me(client, tenant)
    e = (await client.post("/api/webhooks", headers=tenant, json={"url": "https://hooks.example.com/a"})).json()
    assert (await client.patch(f"/api/webhooks/{e['id']}", headers=tenant, json={"active": False})).status_code == 200
    assert (await client.delete(f"/api/webhooks/{e['id']}", headers=tenant)).status_code == 200
    rows = await _rows(db, me["org_id"], "webhook.")
    assert sorted(r.action for r in rows) == ["webhook.create", "webhook.delete", "webhook.update"]
    assert next(r for r in rows if r.action == "webhook.update").diff["fields"] == ["active"]
    assert all(r.actor_id == me["id"] and r.entity_id == e["id"] for r in rows)
    _no_values(rows)


@pytest.mark.asyncio
async def test_sso_changes_are_recorded(client, tenant, db):
    me = await _me(client, tenant)
    c = await client.post("/api/sso/config", headers=tenant, json={
        "protocol": "oidc", "display_name": "Okta", "issuer": "https://idp.example.com",
        "client_id": "cid", "client_secret": "idp-secret", "email_domain": "m9shop.example"})
    assert c.status_code == 201, c.text
    assert (await client.delete(f"/api/sso/config/{c.json()['id']}", headers=tenant)).status_code == 200
    rows = await _rows(db, me["org_id"], "sso.")
    assert sorted(r.action for r in rows) == ["sso.create", "sso.delete"]
    _no_values(rows)


@pytest.mark.asyncio
async def test_email_account_changes_are_recorded(client, tenant, db, monkeypatch):
    monkeypatch.setattr(settings, "GMAIL_CLIENT_ID", "gid")
    monkeypatch.setattr(settings, "GMAIL_CLIENT_SECRET", "gsecret")
    me = await _me(client, tenant)
    assert (await client.post("/api/integrations/email/connect", headers=tenant,
                              json={"provider": "gmail"})).status_code == 200
    conn = (await db.execute(select(EmailConnection).where(EmailConnection.org_id == me["org_id"]))).scalar_one()
    conn.connected = True
    await db.commit()
    assert (await client.post(f"/api/integrations/email/{conn.id}/default", headers=tenant)).status_code == 200
    assert (await client.delete(f"/api/integrations/email/{conn.id}", headers=tenant)).status_code == 200
    rows = await _rows(db, me["org_id"], "email_integration.")
    assert sorted(r.action for r in rows) == ["email_integration.connect_start", "email_integration.default",
                                              "email_integration.disconnect"]


@pytest.mark.asyncio
async def test_platform_admin_changes_to_a_shop_are_in_its_audit_trail(client, tenant, db):
    from backend.db.database import AsyncSessionLocal
    from backend.models import PlatformAdmin
    from backend.services.auth import hash_password
    me = await _me(client, tenant)
    async with AsyncSessionLocal() as s:
        s.add(PlatformAdmin(email="m9ops@test.dev", name="Ops", password_hash=hash_password("portal-pass-123")))
        await s.commit()
    tok = (await client.post("/api/admin/login", json={"email": "m9ops@test.dev",
                                                       "password": "portal-pass-123"})).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    r = await client.patch(f"/api/admin/tenants/{me['org_id']}", headers=h,
                           json={"discount_pct": 20, "admin_notes": "private note"})
    assert r.status_code == 200, r.text
    r = await client.patch(f"/api/admin/tenants/{me['org_id']}/employees/{me['id']}", headers=h,
                           json={"active": True})
    assert r.status_code == 200, r.text
    rows = await _rows(db, me["org_id"], "platform.")
    by = {r.action: r for r in rows}
    assert by["platform.tenant_update"].diff == {"platform_admin": "m9ops@test.dev",
                                                 "fields": ["admin_notes", "discount_pct"]}
    assert "platform.employee_access_update" in by
    assert "private note" not in json.dumps([r.diff for r in rows])
