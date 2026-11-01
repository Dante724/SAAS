"""Batch 1 safety fixes (app-wide audit, Oct 2026).

- Cross-shop reads: a part's stock history and POs, an asset's history and the
  asset-to-product prefill were readable by another shop that knew the id.
- QuickBooks payment sync could push another shop's payment.
- A teammate with the same access as the owner could reset the owner's password or 2-step.
- Assigning a role checked permissions but not data scope (locations / own orders).
- Admin actions on staff, roles and API keys left no audit trail.
- Any staff member could delete a customer's signature from the order feed.
"""
import uuid

import pytest
from sqlalchemy import select


async def _register(client):
    email = f"b1-{uuid.uuid4().hex[:8]}@t.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Co", "name": "Owner", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    body = r.json()
    H = {"Authorization": f"Bearer {body['access_token']}"}
    me = (await client.get("/api/auth/me", headers=H)).json()
    return H, body["org_id"], me["id"]


async def _login(client, email, password="pw-test-12345"):
    r = await client.post("/api/auth/token", data={"username": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _admin_role_id(db, org_id):
    from backend.models import Role
    return (await db.execute(select(Role.id).where(Role.org_id == org_id, Role.name == "admin"))).scalar_one()


async def _teammate(client, H, role_id, email=None):
    email = email or f"tm-{uuid.uuid4().hex[:8]}@t.dev"
    r = await client.post("/api/staff/employees", json={
        "name": "Teammate", "email": email, "password": "pw-test-12345", "role_id": role_id}, headers=H)
    assert r.status_code == 201, r.text
    return r.json()["id"], await _login(client, email)


# ── cross-shop reads ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_part_history_and_pos_are_shop_only(client):
    A, _, _ = await _register(client)
    B, _, _ = await _register(client)
    part = (await client.post("/api/inventory/parts", json={"name": "Screen"}, headers=A)).json()["id"]

    assert (await client.get(f"/api/inventory/parts/{part}/movements", headers=A)).status_code == 200
    assert (await client.get(f"/api/inventory/parts/{part}/movements", headers=B)).status_code == 404
    assert (await client.get(f"/api/inventory/parts/{part}/purchase-orders", headers=B)).json()["items"] == []


@pytest.mark.asyncio
async def test_asset_history_and_prefill_are_shop_only(client):
    A, _, _ = await _register(client)
    B, _, _ = await _register(client)
    r = await client.post("/api/assets", json={
        "owner_type": "company", "asset_type": "Device", "brand": "Acme", "model": "X1"}, headers=A)
    asset = r.json()["id"]

    assert (await client.get(f"/api/assets/{asset}/movements", headers=A)).status_code == 200
    assert (await client.get(f"/api/assets/{asset}/movements", headers=B)).status_code == 404
    assert (await client.get(f"/api/conversions/prefill/asset/{asset}", headers=A)).status_code == 200
    assert (await client.get(f"/api/conversions/prefill/asset/{asset}", headers=B)).status_code == 404


@pytest.mark.asyncio
async def test_qbo_payment_sync_only_pushes_own_payments(client, db):
    from backend.models import Client, Payment
    from backend.services.qbo import _payment_in_org
    _, org_a, _ = await _register(client)
    _, org_b, _ = await _register(client)
    c = Client(org_id=org_a, name="Ravi")
    db.add(c)
    await db.flush()
    p = Payment(client_id=c.id, method="cash", amount=100)
    db.add(p)
    await db.commit()

    assert await _payment_in_org(db, org_a, p)
    assert not await _payment_in_org(db, org_b, p)
    assert not await _payment_in_org(db, org_a, Payment(method="cash", amount=1))   # unowned: refuse


# ── owner account and role scope ──────────────────────────────────

@pytest.mark.asyncio
async def test_full_access_teammate_cannot_take_over_owner(client, db):
    H, org, owner_id = await _register(client)
    _, T = await _teammate(client, H, await _admin_role_id(db, org))

    assert (await client.post(f"/api/staff/employees/{owner_id}/password",
                              json={"password": "taken-over-123"}, headers=T)).status_code == 403
    assert (await client.post(f"/api/staff/employees/{owner_id}/reset-mfa", headers=T)).status_code == 403
    assert (await client.post(f"/api/staff/employees/{owner_id}/send-reset", headers=T)).status_code == 403
    assert (await client.patch(f"/api/staff/employees/{owner_id}",
                               json={"email": "evil@t.dev"}, headers=T)).status_code == 403
    assert (await client.delete(f"/api/staff/employees/{owner_id}", headers=T)).status_code == 403
    # the owner can still manage their own account
    assert (await client.patch(f"/api/staff/employees/{owner_id}", json={"phone": "123"}, headers=H)).status_code == 200


@pytest.mark.asyncio
async def test_owner_is_recorded_at_signup(client, db):
    from backend.models import Organisation
    _, org, owner_id = await _register(client)
    assert (await db.execute(select(Organisation.owner_id).where(Organisation.id == org))).scalar_one() == owner_id


@pytest.mark.asyncio
async def test_cannot_assign_role_with_wider_data_scope(client):
    H, _, _ = await _register(client)
    narrow = (await client.post("/api/settings/rbac/roles", json={
        "name": "Own orders manager", "order_scope": "own"}, headers=H)).json()["id"]
    wide = (await client.post("/api/settings/rbac/roles", json={
        "name": "Front desk", "permissions": {"can_view_finance": False}}, headers=H)).json()["id"]
    _, M = await _teammate(client, H, narrow)

    r = await client.post("/api/staff/employees", json={
        "name": "New", "email": f"n-{uuid.uuid4().hex[:6]}@t.dev", "password": "pw-test-12345",
        "role_id": wide}, headers=M)
    assert r.status_code == 403
    assert "wider" in r.json()["detail"]


# ── audit trail ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_staff_role_and_key_admin_actions_are_audited(client, db):
    from backend.models import AuditLog
    H, org, _ = await _register(client)
    emp_id, _ = await _teammate(client, H, await _admin_role_id(db, org))
    await client.post(f"/api/staff/employees/{emp_id}/password", json={"password": "new-pass-1234"}, headers=H)
    await client.post(f"/api/staff/employees/{emp_id}/reset-mfa", headers=H)
    await client.post("/api/settings/rbac/roles", json={"name": "Tech"}, headers=H)
    await client.post("/api/api-keys", json={"name": "Zapier"}, headers=H)

    actions = set((await db.execute(select(AuditLog.action).where(AuditLog.org_id == org))).scalars().all())
    assert {"employee.create", "employee.password_set", "employee.mfa_reset", "role.create", "apikey.create"} <= actions


# ── order feed ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_signatures_cannot_be_deleted_but_own_comments_can(client, db):
    from backend.models import EventFeedItem
    H, org, _ = await _register(client)
    cl = (await client.post("/api/clients", json={"name": "Ravi"}, headers=H)).json()["id"]
    sig = EventFeedItem(org_id=org, entity_type="client", entity_id=cl, item_type="signature",
                        visibility="client", author_name="Customer")
    db.add(sig)
    await db.commit()
    assert (await client.delete(f"/api/feed/item/{sig.id}", headers=H)).status_code == 403

    note = (await client.post(f"/api/feed/client/{cl}", json={"item_type": "comment", "body": "Called"}, headers=H)).json()
    assert (await client.delete(f"/api/feed/item/{note['id']}", headers=H)).status_code == 200
