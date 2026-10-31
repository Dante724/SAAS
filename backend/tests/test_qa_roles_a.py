"""QA fix plan A (31 Oct 2026, 5-shop run): roles and permissions.

1. A role created with a partial permission map gets only what was sent (on top of its template).
2. The technician (Specialist/Partner) template keeps money to itself; PO cost is redacted.
3. A technician can't silently "edit" a line's cost price, or close a job that still owes money.
4. Invoicing from a job uses the invoice codes; done -> delivered isn't "editing a closed job".
5. A technician doesn't read the team list's contact details, invites or the role list.
"""
import uuid

import pytest
from sqlalchemy import select

from backend.models import Role
from backend.services import permissions as P


async def _owner(client):
    r = await client.post("/api/auth/register", json={"org_name": "Shop", "name": "Owner",
                                                      "email": f"o-{uuid.uuid4().hex[:8]}@test.dev", "password": "pw-test-12345"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _person(client, H, role_id, name="Pat"):
    email = f"{name.lower()}-{uuid.uuid4().hex[:6]}@test.dev"
    r = await client.post("/api/staff/employees", json={"name": name, "email": email, "password": "pw-test-12345",
                                                         "role_id": role_id}, headers=H)
    assert r.status_code == 201, r.text
    tok = await client.post("/api/auth/token", data={"username": email, "password": "pw-test-12345"})
    return {"Authorization": f"Bearer {tok.json()['access_token']}"}


async def _role(client, H, body):
    r = await client.post("/api/settings/rbac/roles", json=body, headers=H)
    assert r.status_code == 201, r.text
    return r.json()


# ── 1. new roles start from their template (or nothing), plus what was sent ─────────────────

def test_new_role_without_template_starts_with_nothing():
    perms = P.new_role_permissions(None, {"can_create_tickets": True, "can_view_cashflow": False})
    assert {c for c, v in perms.items() if v} == {"can_create_tickets"}
    assert set(perms) >= P.ALL_CODES


def test_new_role_from_template_keeps_the_template():
    perms = P.new_role_permissions("Specialist", {"can_view_reports": True})
    assert perms["can_create_tickets"] is True and perms["can_manage_roles"] is False
    assert perms["can_view_reports"] is True
    assert P.new_role_permissions("Full access") == {c: c not in P.OPT_IN for c in P.ALL_CODES}


@pytest.mark.asyncio
async def test_partial_map_through_both_endpoints_grants_only_what_was_sent(client):
    H = await _owner(client)
    sent = {"can_create_tickets": True, "can_add_products": True, "display_in_assignees": True}
    r = await client.post("/api/staff/roles", json={"name": "Tech", "permissions": sent}, headers=H)
    assert r.status_code == 201, r.text
    T = await _person(client, H, r.json()["id"])
    me = (await client.get("/api/auth/me", headers=T)).json()["permissions"]
    assert {c for c, v in me.items() if v} == set(sent)
    # the role editor path behaves the same
    role = await _role(client, H, {"name": "Desk", "permissions": sent})
    assert {c for c, v in role["permissions"].items() if v} == set(sent)


@pytest.mark.asyncio
async def test_existing_roles_keep_their_stored_permissions(client, db):
    H = await _owner(client)
    role = await _role(client, H, {"name": "Old", "template": "Full access"})
    T = await _person(client, H, role["id"])
    before = (await client.get("/api/auth/me", headers=T)).json()["permissions"]
    assert before["can_manage_settings"] is True
    r = await client.put(f"/api/settings/rbac/roles/{role['id']}",
                         json={"name": "Old", "permissions": {"can_view_cashflow": False}}, headers=H)
    assert r.status_code == 200, r.text
    after = (await client.get("/api/auth/me", headers=T)).json()["permissions"]
    assert after == {**before, "can_view_cashflow": False}

