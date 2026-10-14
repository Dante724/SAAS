"""Builds 81-83 — feedback report & chat→order, CRM data ops, record-level scoping."""
import pytest
from sqlalchemy import select

from backend.models import Client, Employee, Feedback, Order, Role
from backend.services.auth import hash_password


async def _me(client, H):
    return (await client.get("/api/auth/me", headers=H)).json()


async def _loc(client, H):
    return (await client.get("/api/staff/locations", headers=H)).json()["items"][0]["id"]


async def _scoped_login(client, db, org_id, *, email, **role_kw):
    """Create a role with the given scope + an employee holding it, and log in."""
    role = Role(org_id=org_id, name="Scoped", permissions={}, **role_kw)
    db.add(role)
    await db.flush()
    emp = Employee(org_id=org_id, name="Scoped user", email=email,
                   password_hash=hash_password("pw-123456789"), active=True, role_id=role.id)
    db.add(emp)
    await db.commit()
    await db.refresh(emp)
    tok = (await client.post("/api/auth/token", data={
        "username": email, "password": "pw-123456789"})).json()["access_token"]
    return {"Authorization": f"Bearer {tok}"}, emp, role


# ── Build 81: feedback report ────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_feedback_report_average_and_distribution(client, tenant, db):
    me = await _me(client, tenant)
    loc = await _loc(client, tenant)
    o = (await client.post("/api/orders", json={"location_id": loc, "complaint": "x"},
                           headers=tenant)).json()
    from datetime import datetime
    for rating in (5, 4, 5):
        db.add(Feedback(org_id=me["org_id"], order_id=o["id"], system="five_point",
                        rating=rating, submitted_at=datetime.utcnow()))
    db.add(Feedback(org_id=me["org_id"], order_id=o["id"], system="five_point"))  # not submitted
    await db.commit()

    r = (await client.get("/api/feedback/report", headers=tenant)).json()
    assert r["total"] == 3                       # unsubmitted excluded
    assert r["average"] == pytest.approx(4.67, abs=0.01)
    assert r["distribution"] == {"5": 2, "4": 1}
    assert r["nps"] is None                      # NPS is meaningless for 5-point


@pytest.mark.asyncio
async def test_feedback_report_computes_real_nps(client, tenant, db):
    """NPS = %promoters − %detractors, which an average can't express."""
    from datetime import datetime
    me = await _me(client, tenant)
    loc = await _loc(client, tenant)
    o = (await client.post("/api/orders", json={"location_id": loc, "complaint": "x"},
                           headers=tenant)).json()
    for rating in (10, 9, 8, 3):     # 2 promoters, 1 passive, 1 detractor
        db.add(Feedback(org_id=me["org_id"], order_id=o["id"], system="nps",
                        rating=rating, submitted_at=datetime.utcnow()))
    await db.commit()
    r = (await client.get("/api/feedback/report", headers=tenant)).json()
    assert r["promoters"] == 2 and r["passives"] == 1 and r["detractors"] == 1
    assert r["nps"] == 25            # (2−1)/4 = 25%


@pytest.mark.asyncio
async def test_empty_feedback_report(client, tenant):
    r = (await client.get("/api/feedback/report", headers=tenant)).json()
    assert r["total"] == 0 and r["average"] is None and r["nps"] is None


# ── Build 81: chat → work order ──────────────────────────────────────────────
@pytest.mark.asyncio
async def test_chat_to_order_creates_client_and_order(client, tenant, db):
    t = await client.post("/api/chats/threads", json={
        "channel": "whatsapp", "contact_phone": "+15550001", "contact_name": "Walk-in"},
        headers=tenant)
    assert t.status_code == 201, t.text
    tid = t.json()["id"]
    await client.post(f"/api/chats/threads/{tid}/inbound", json={
        "body": "My screen is cracked"}, headers=tenant)

    r = await client.post(f"/api/chats/threads/{tid}/to-order", headers=tenant)
    assert r.status_code == 201, r.text
    oid = r.json()["order_id"]

    order = (await db.execute(select(Order).where(Order.id == oid))).scalar_one()
    assert order.complaint == "My screen is cracked"     # seeded from first inbound
    assert order.client_id == r.json()["client_id"]
    c = (await db.execute(select(Client).where(Client.id == order.client_id))).scalar_one()
    assert c.phone == "+15550001"                        # client created from the chat contact

    dupe = await client.post(f"/api/chats/threads/{tid}/to-order", headers=tenant)
    assert dupe.status_code == 409                       # one order per thread


# ── Build 82: client import / merge / export ─────────────────────────────────
@pytest.mark.asyncio
async def test_import_maps_columns_and_creates(client, tenant):
    body = {
        "rows": [{"Full name": "Ann Lee", "Mobile": "+15551", "Mail": "ann@x.dev"},
                 {"Full name": "Bob Ray", "Mobile": "+15552"}],
        "mapping": {"Full name": "name", "Mobile": "phone", "Mail": "email"},
    }
    r = await client.post("/api/clients/import", json=body, headers=tenant)
    assert r.status_code == 200, r.text
    assert r.json()["created"] == 2 and r.json()["errors"] == []


@pytest.mark.asyncio
async def test_import_updates_existing_by_phone(client, tenant):
    await client.post("/api/clients", json={"name": "Old Name", "phone": "+15559"}, headers=tenant)
    body = {
        "rows": [{"n": "New Name", "p": "+15559", "e": "new@x.dev"}],
        "mapping": {"n": "name", "p": "phone", "e": "email"},
        "update_existing": True,
    }
    r = await client.post("/api/clients/import", json=body, headers=tenant)
    assert r.json()["updated"] == 1 and r.json()["created"] == 0   # matched on phone, no duplicate

    lst = (await client.get("/api/clients?search=%2B15559", headers=tenant)).json()
    assert lst["total"] == 1 and lst["items"][0]["name"] == "New Name"


@pytest.mark.asyncio
async def test_import_dry_run_writes_nothing(client, tenant):
    body = {"rows": [{"n": "Ghost", "p": "+15558"}], "mapping": {"n": "name", "p": "phone"},
            "dry_run": True}
    r = await client.post("/api/clients/import", json=body, headers=tenant)
    assert r.json()["dry_run"] is True and r.json()["created"] == 1
    assert r.json()["preview"][0]["action"] == "create"
    lst = (await client.get("/api/clients?search=Ghost", headers=tenant)).json()
    assert lst["total"] == 0            # preview only — nothing written


@pytest.mark.asyncio
async def test_import_rejects_unknown_mapping_and_bad_rows(client, tenant):
    bad = await client.post("/api/clients/import", json={
        "rows": [], "mapping": {"x": "salary"}}, headers=tenant)
    assert bad.status_code == 422

    r = await client.post("/api/clients/import", json={
        "rows": [{"n": ""}, {"n": "No contact"}],
        "mapping": {"n": "name"}}, headers=tenant)
    assert r.json()["skipped"] == 2      # missing name; missing phone+email
    assert len(r.json()["errors"]) == 2


@pytest.mark.asyncio
async def test_merge_clients_moves_history(client, tenant, db):
    loc = await _loc(client, tenant)
    a = (await client.post("/api/clients", json={"name": "Dup A", "phone": "+16001"},
                           headers=tenant)).json()
    b = (await client.post("/api/clients", json={"name": "Dup B", "phone": "+16002",
                                                 "email": "b@x.dev"}, headers=tenant)).json()
    o = await client.post("/api/orders", json={
        "client_id": b["id"], "location_id": loc, "complaint": "x"}, headers=tenant)
    assert o.status_code == 201

    r = await client.post("/api/clients/merge", json={
        "target_id": a["id"], "source_ids": [b["id"]]}, headers=tenant)
    assert r.status_code == 200, r.text
    assert r.json()["moved"]["orders"] == 1

    target = (await db.execute(select(Client).where(Client.id == a["id"]))).scalar_one()
    await db.refresh(target)
    assert target.email == "b@x.dev"        # blank filled from source
    assert target.name == "Dup A"           # real value not overwritten
    order = (await db.execute(select(Order).where(Order.id == o.json()["id"]))).scalar_one()
    await db.refresh(order)
    assert order.client_id == a["id"]       # history re-pointed
    gone = (await db.execute(select(Client).where(Client.id == b["id"]))).scalar_one_or_none()
    assert gone is None                     # source removed after history moved


@pytest.mark.asyncio
async def test_export_csv(client, tenant):
    await client.post("/api/clients", json={"name": "Exportable", "phone": "+16009"},
                      headers=tenant)
    r = await client.get("/api/clients/export/csv", headers=tenant)
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    assert "Exportable" in r.text and "name,phone,email" in r.text


# ── Build 83: record-level scoping ───────────────────────────────────────────
@pytest.mark.asyncio
async def test_contact_scope_own_hides_other_contacts(client, tenant, db):
    me = await _me(client, tenant)
    H, emp, _ = await _scoped_login(client, db, me["org_id"],
                                    email="contacts@x.dev", contact_scope="own")
    mine = (await client.post("/api/clients", json={"name": "Mine", "phone": "+17001"},
                              headers=tenant)).json()
    theirs = (await client.post("/api/clients", json={"name": "Theirs", "phone": "+17002"},
                                headers=tenant)).json()
    row = (await db.execute(select(Client).where(Client.id == mine["id"]))).scalar_one()
    row.manager_id = emp.id            # this one is managed by the scoped user
    await db.commit()

    lst = (await client.get("/api/clients", headers=H)).json()
    names = {c["name"] for c in lst["items"]}
    assert names == {"Mine"}
    assert lst["total"] == 1           # the count respects the scope too

    assert (await client.get(f"/api/clients/{mine['id']}", headers=H)).status_code == 200
    assert (await client.get(f"/api/clients/{theirs['id']}", headers=H)).status_code == 403

    # the full-access owner still sees everything
    assert (await client.get("/api/clients", headers=tenant)).json()["total"] >= 2


@pytest.mark.asyncio
async def test_order_scope_own_hides_other_orders(client, tenant, db):
    me = await _me(client, tenant)
    loc = await _loc(client, tenant)
    H, emp, _ = await _scoped_login(client, db, me["org_id"],
                                    email="orders@x.dev", order_scope="own")
    mine = (await client.post("/api/orders", json={
        "location_id": loc, "complaint": "mine", "assigned_to_id": emp.id}, headers=tenant)).json()
    theirs = (await client.post("/api/orders", json={
        "location_id": loc, "complaint": "theirs", "assigned_to_id": me["id"]},
        headers=tenant)).json()

    lst = (await client.get("/api/orders", headers=H)).json()
    ids = {o["id"] for o in lst["items"]}
    assert mine["id"] in ids and theirs["id"] not in ids

    assert (await client.get(f"/api/orders/{mine['id']}", headers=H)).status_code == 200
    assert (await client.get(f"/api/orders/{theirs['id']}", headers=H)).status_code == 403


@pytest.mark.asyncio
async def test_default_scope_changes_nothing(client, tenant, db):
    """A role that leaves scopes at 'all' behaves exactly as before."""
    me = await _me(client, tenant)
    loc = await _loc(client, tenant)
    H, _, _ = await _scoped_login(client, db, me["org_id"], email="allscope@x.dev")
    await client.post("/api/clients", json={"name": "Visible", "phone": "+17009"}, headers=tenant)
    await client.post("/api/orders", json={"location_id": loc, "complaint": "x"}, headers=tenant)
    assert (await client.get("/api/clients", headers=H)).json()["total"] >= 1
    assert (await client.get("/api/orders", headers=H)).json()["total"] >= 1


@pytest.mark.asyncio
async def test_warehouse_scope_blocks_stock_moves(client, tenant, db):
    from backend.models import Location
    me = await _me(client, tenant)
    loc = await _loc(client, tenant)
    other = Location(org_id=me["org_id"], name="Warehouse B")
    db.add(other)
    await db.commit()
    await db.refresh(other)

    H, _, _ = await _scoped_login(client, db, me["org_id"], email="wh@x.dev",
                                  warehouse_scope="selected", warehouse_ids=[other.id])
    part = (await client.post("/api/inventory/parts", json={
        "name": "Widget", "sku": "WS-1"}, headers=tenant)).json()

    denied = await client.post(f"/api/inventory/parts/{part['id']}/adjust", json={
        "location_id": loc, "qty": 1, "movement_type": "receive"}, headers=H)
    assert denied.status_code == 403          # not an allowed warehouse

    ok = await client.post(f"/api/inventory/parts/{part['id']}/adjust", json={
        "location_id": other.id, "qty": 1, "movement_type": "receive"}, headers=H)
    assert ok.status_code in (200, 201)       # allowed warehouse works


@pytest.mark.asyncio
async def test_role_scope_roundtrips_through_api(client, tenant):
    r = await client.post("/api/settings/rbac/roles", json={
        "name": "Field tech", "permissions": {},
        "contact_scope": "own", "order_scope": "own",
        "warehouse_scope": "selected", "warehouse_ids": ["w1"],
        "payroll_visibility_days": 90}, headers=tenant)
    assert r.status_code == 201, r.text
    got = r.json()
    assert got["contact_scope"] == "own" and got["order_scope"] == "own"
    assert got["warehouse_ids"] == ["w1"] and got["payroll_visibility_days"] == 90

    bad = await client.post("/api/settings/rbac/roles", json={
        "name": "Bad", "contact_scope": "nonsense"}, headers=tenant)
    assert bad.status_code == 422


@pytest.mark.asyncio
async def test_employee_groups_crud(client, tenant):
    me = await _me(client, tenant)
    g = await client.post("/api/settings/rbac/groups", json={
        "name": "Front desk", "member_ids": [me["id"]]}, headers=tenant)
    assert g.status_code == 201, g.text
    gid = g.json()["id"]
    lst = (await client.get("/api/settings/rbac/groups", headers=tenant)).json()
    assert any(x["id"] == gid and x["member_ids"] == [me["id"]] for x in lst["items"])

    u = await client.put(f"/api/settings/rbac/groups/{gid}", json={
        "name": "Front desk (AM)", "member_ids": []}, headers=tenant)
    assert u.status_code == 200 and u.json()["name"] == "Front desk (AM)"
    assert (await client.delete(f"/api/settings/rbac/groups/{gid}", headers=tenant)).status_code == 200
    assert (await client.get("/api/settings/rbac/groups", headers=tenant)).json()["items"] == []
