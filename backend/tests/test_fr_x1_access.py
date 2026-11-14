"""Final-review fix wave, group X1: access control on the public booking slug, teammate
management, role scope, API keys and the customer portal.

SEC-01  a shop could set its subdomain to another shop's org id and take over that shop's
        booking page (and the order lookup's `shop=`).
SEC-02  a location/record/cashbox-limited manager could reset the password or 2FA of a wider
        teammate and sign in as them.
SEC-03  the role editor's "not wider than mine" check ignored cashboxes, the actual warehouses
        and hidden statuses.
SEC-09  an API key ignored its creator's scope and kept working after the creator was narrowed.
QA-7    the customer portal listed archived (deleted) jobs, unbounded, with per-job queries.
"""
import json
import uuid

import pytest
from sqlalchemy import event, select

from backend.models import Employee, Order, Organisation, Payment, Role

PW = "pw-probe-123456"


async def _register(client, org):
    r = await client.post("/api/auth/register", json={
        "org_name": org, "name": "Owner", "email": f"{uuid.uuid4().hex[:8]}@x.test", "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}, r.json()["org_id"]


async def _login(client, email, pw=PW):
    return await client.post("/api/auth/token", data={"username": email, "password": pw})


async def _hdr(client, email, pw=PW):
    r = await _login(client, email, pw)
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _role(client, h, **body):
    body.setdefault("name", f"R {uuid.uuid4().hex[:6]}")
    r = await client.post("/api/settings/rbac/roles", headers=h, json=body)
    return r


async def _staff(client, h, role_id, nm="x"):
    em = f"{nm}-{uuid.uuid4().hex[:6]}@test.dev"
    r = await client.post("/api/staff/employees", headers=h, json={
        "name": nm, "email": em, "password": PW, "role_id": role_id})
    assert r.status_code == 201, r.text
    return r.json()["id"], em


async def _first_location(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


MGR = {"can_manage_employees": True, "can_manage_roles": True, "can_view_finance": True}


# ── SEC-01: public slug can't shadow another shop's id ─────────────────────────────────────────
@pytest.mark.asyncio
async def test_sec01_subdomain_cannot_be_another_shops_id(client):
    ha, _ = await _register(client, "Attacker Shop")
    hb, org_b = await _register(client, "Victim Shop")
    slug_b = (await client.get("/api/staff/public-pages", headers=hb)).json()["slug"]
    assert slug_b == org_b
    r = await client.put("/api/staff/public-pages/subdomain", headers=ha, json={"subdomain": slug_b})
    assert r.status_code == 422, r.text
    page = await client.get(f"/api/public/appointment/{slug_b}")
    assert page.status_code == 200 and "Victim Shop" in page.text and "Attacker Shop" not in page.text


@pytest.mark.asyncio
@pytest.mark.parametrize("slug", [
    "0b5e2f8a-1c3d-4e5f-8a9b-0c1d2e3f4a5b",      # any id-shaped value
    "0B5E2F8A-1C3D-4E5F-8A9B-0C1D2E3F4A5B",
    "0b5e2f8a1c3d4e5f8a9b0c1d2e3f4a5b",          # hex without dashes
    "www", "api", "admin", "book", "portal",     # reserved words
])
async def test_sec01_id_shaped_and_reserved_subdomains_are_refused(client, tenant, slug):
    r = await client.put("/api/staff/public-pages/subdomain", headers=tenant, json={"subdomain": slug})
    assert r.status_code == 422, (slug, r.text)


@pytest.mark.asyncio
async def test_sec01_ordinary_subdomain_still_works(client, tenant):
    r = await client.put("/api/staff/public-pages/subdomain", headers=tenant, json={"subdomain": "fix-it-42"})
    assert r.status_code == 200 and r.json()["subdomain"] == "fix-it-42"


@pytest.mark.asyncio
async def test_sec01_existing_hijack_row_cannot_shadow_the_id_link(client, db):
    """A subdomain claimed before this fix still can't win: a shop's id resolves to that shop."""
    _, org_a = await _register(client, "Attacker Shop")
    hb, org_b = await _register(client, "Victim Shop")
    a = await db.get(Organisation, org_a)
    a.subdomain = org_b
    await db.commit()
    page = await client.get(f"/api/public/appointment/{org_b}")
    assert page.status_code == 200 and "Victim Shop" in page.text and "Attacker Shop" not in page.text

    # The order lookup's shop= resolves the same way: B's own order is found under B's id.
    cid = (await client.post("/api/clients", json={"name": "Pat", "phone": "5550001111"}, headers=hb)).json()["id"]
    o = (await client.post("/api/orders", json={"client_id": cid, "complaint": "screen"}, headers=hb)).json()
    r = await client.post("/api/public/lookup", json={
        "order_number": o["order_number"], "phone": "5550001111", "shop": org_b})
    assert r.status_code == 200, r.text


# ── SEC-02: no managing a wider teammate ───────────────────────────────────────────────────────
async def _scoped_and_wide(client, tenant):
    loc = await _first_location(client, tenant)
    scoped = await _role(client, tenant, permissions=MGR, location_scope="selected", location_ids=[loc],
                         order_scope="own", contact_scope="own")
    wide = await _role(client, tenant, permissions=MGR)
    assert scoped.status_code == 201 and wide.status_code == 201
    m = await _staff(client, tenant, scoped.json()["id"], "m")
    t = await _staff(client, tenant, wide.json()["id"], "t")
    return m, t, scoped.json()["id"], wide.json()["id"]


@pytest.mark.asyncio
async def test_sec02_scoped_manager_cannot_take_over_wider_teammate(client, tenant):
    m, t, _, _ = await _scoped_and_wide(client, tenant)
    hm = await _hdr(client, m[1])
    r = await client.post(f"/api/staff/employees/{t[0]}/password", headers=hm, json={"password": "taken-over-9999"})
    assert r.status_code == 403, r.text
    assert (await _login(client, t[1], "taken-over-9999")).status_code != 200
    r = await client.post(f"/api/staff/employees/{t[0]}/reset-mfa", headers=hm, json={})
    assert r.status_code == 403, r.text
    r = await client.patch(f"/api/staff/employees/{t[0]}", headers=hm, json={"name": "renamed"})
    assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_sec02_cashbox_limited_manager_cannot_manage_all_tills_teammate(client, tenant):
    aid = (await client.post("/api/cash/accounts", headers=tenant, json={"name": "Till A"})).json()["id"]
    lim = await _role(client, tenant, permissions=MGR, account_ids=[aid])
    wide = await _role(client, tenant, permissions=MGR)
    m = await _staff(client, tenant, lim.json()["id"], "m")
    t = await _staff(client, tenant, wide.json()["id"], "t")
    hm = await _hdr(client, m[1])
    r = await client.post(f"/api/staff/employees/{t[0]}/password", headers=hm, json={"password": "taken-over-9999"})
    assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_sec02_scoped_manager_can_still_manage_an_equally_scoped_teammate(client, tenant):
    m, _, scoped_id, _ = await _scoped_and_wide(client, tenant)
    peer = await _staff(client, tenant, scoped_id, "p")
    hm = await _hdr(client, m[1])
    r = await client.post(f"/api/staff/employees/{peer[0]}/password", headers=hm, json={"password": "fresh-pass-9999"})
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_sec02_selected_with_no_locations_counts_as_wider(client, tenant, db):
    """A legacy role saved as location_scope=selected with no locations was read as "every location"
    by some screens: a location-limited manager must not manage or hand out such a role."""
    m, _, _, _ = await _scoped_and_wide(client, tenant)
    me = await db.get(Employee, (await db.execute(select(Employee.id).where(
        Employee.email == m[1]))).scalar_one())
    legacy = Role(org_id=me.org_id, name=f"legacy-{uuid.uuid4().hex[:4]}", permissions=MGR, perms_explicit=True,
                  location_scope="selected", location_ids=None)
    db.add(legacy)
    await db.commit()
    t = await _staff(client, tenant, legacy.id, "t")
    hm = await _hdr(client, m[1])
    r = await client.post(f"/api/staff/employees/{t[0]}/password", headers=hm, json={"password": "taken-over-9999"})
    assert r.status_code == 403, r.text
    r = await client.post("/api/staff/employees", headers=hm, json={
        "name": "n", "email": f"n-{uuid.uuid4().hex[:6]}@test.dev", "password": PW, "role_id": legacy.id})
    assert r.status_code == 403, r.text


# ── SEC-03: role scope includes cashboxes, warehouses and hidden statuses ─────────────────────
@pytest.mark.asyncio
async def test_sec03_cashbox_restricted_editor_cannot_create_all_tills_role(client, tenant):
    a = (await client.post("/api/cash/accounts", headers=tenant, json={"name": "Till A"})).json()["id"]
    b = (await client.post("/api/cash/accounts", headers=tenant, json={"name": "Till B"})).json()["id"]
    r = await _role(client, tenant, permissions=MGR, account_ids=[a])
    c = await _staff(client, tenant, r.json()["id"], "c")
    hc = await _hdr(client, c[1])
    assert (await _role(client, hc, permissions=MGR)).status_code == 403            # [] = every till
    assert (await _role(client, hc, permissions=MGR, account_ids=[a, b])).status_code == 403
    ok = await _role(client, hc, permissions=MGR, account_ids=[a])
    assert ok.status_code == 201, ok.text


@pytest.mark.asyncio
async def test_sec03_warehouse_ids_must_be_a_subset(client, tenant):
    w1 = await _first_location(client, tenant)
    w2 = (await client.post("/api/staff/locations", headers=tenant, json={"name": "Second"})).json()["id"]
    r = await _role(client, tenant, permissions=MGR, warehouse_scope="selected", warehouse_ids=[w1])
    assert r.status_code == 201, r.text
    c = await _staff(client, tenant, r.json()["id"], "c")
    hc = await _hdr(client, c[1])
    bad = await _role(client, hc, permissions=MGR, warehouse_scope="selected", warehouse_ids=[w2])
    assert bad.status_code == 403, bad.text
    ok = await _role(client, hc, permissions=MGR, warehouse_scope="selected", warehouse_ids=[w1])
    assert ok.status_code == 201, ok.text


@pytest.mark.asyncio
async def test_sec03_hidden_statuses_must_stay_hidden(client, tenant):
    r = await _role(client, tenant, permissions=MGR, hidden_status_keys=["done"])
    c = await _staff(client, tenant, r.json()["id"], "c")
    hc = await _hdr(client, c[1])
    assert (await _role(client, hc, permissions=MGR)).status_code == 403
    ok = await _role(client, hc, permissions=MGR, hidden_status_keys=["done", "new"])
    assert ok.status_code == 201, ok.text


@pytest.mark.asyncio
async def test_sec03_cashbox_limited_manager_cannot_assign_an_all_tills_role(client, tenant):
    a = (await client.post("/api/cash/accounts", headers=tenant, json={"name": "Till A"})).json()["id"]
    lim = await _role(client, tenant, permissions=MGR, account_ids=[a])
    wide = await _role(client, tenant, permissions=MGR)
    c = await _staff(client, tenant, lim.json()["id"], "c")
    hc = await _hdr(client, c[1])
    r = await client.post("/api/staff/employees", headers=hc, json={
        "name": "d", "email": f"d-{uuid.uuid4().hex[:6]}@test.dev", "password": PW, "role_id": wide.json()["id"]})
    assert r.status_code == 403, r.text


# ── SEC-09: API keys follow their creator's current access ─────────────────────────────────────
KEYMGR = {"can_manage_settings": True, "can_view_client_info": True}


@pytest.mark.asyncio
async def test_sec09_scoped_creator_cannot_mint_a_shop_wide_key(client, tenant):
    loc = await _first_location(client, tenant)
    r = await _role(client, tenant, permissions=KEYMGR, location_scope="selected", location_ids=[loc])
    c = await _staff(client, tenant, r.json()["id"], "k")
    hc = await _hdr(client, c[1])
    r = await client.post("/api/api-keys", json={"name": "zap", "scopes": "read"}, headers=hc)
    assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_sec09_key_stops_when_creator_is_narrowed_and_resumes_when_restored(client, tenant, db):
    r = await _role(client, tenant, permissions=KEYMGR)
    role_id = r.json()["id"]
    c = await _staff(client, tenant, role_id, "k")
    hc = await _hdr(client, c[1])
    key = (await client.post("/api/api-keys", json={"name": "zap", "scopes": "read"}, headers=hc)).json()["key"]
    kh = {"Authorization": f"Bearer {key}"}
    assert (await client.get("/api/v1/contacts", headers=kh)).status_code == 200

    role = await db.get(Role, role_id)
    role.order_scope = "own"                         # narrowed: the key no longer acts shop-wide
    await db.commit()
    assert (await client.get("/api/v1/contacts", headers=kh)).status_code == 401

    role.order_scope = "all"
    await db.commit()
    assert (await client.get("/api/v1/contacts", headers=kh)).status_code == 200

    role.permissions = {**role.permissions, "can_manage_settings": False}   # lost the right to hold keys
    await db.commit()
    assert (await client.get("/api/v1/contacts", headers=kh)).status_code == 401


@pytest.mark.asyncio
async def test_sec09_costs_scope_follows_creators_current_cost_permission(client, tenant, db):
    from backend.services.apikeys import creator_key_scopes
    r = await _role(client, tenant, permissions={**KEYMGR, "can_view_purchase_price": True})
    role_id = r.json()["id"]
    c = await _staff(client, tenant, role_id, "k")
    emp = await db.get(Employee, (await db.execute(select(Employee.id).where(Employee.email == c[1]))).scalar_one())
    assert "costs:read" in await creator_key_scopes(db, emp, {"read", "costs:read"})
    role = await db.get(Role, role_id)
    role.permissions = {**role.permissions, "can_view_purchase_price": False}
    await db.commit()
    assert await creator_key_scopes(db, emp, {"read", "costs:read"}) == {"read"}


# ── QA-7: customer portal hides archived jobs and is bounded ──────────────────────────────────
async def _portal(client, h, cid):
    body = (await client.post(f"/api/clients/{cid}/portal-link", headers=h)).json()
    return body.get("portal_token") or body.get("token")


@pytest.mark.asyncio
async def test_qa7_portal_hides_archived_jobs(client, tenant):
    cid = (await client.post("/api/clients", json={"name": "Pat Doe", "phone": "5550001111"}, headers=tenant)).json()["id"]
    keep = (await client.post("/api/orders", json={"client_id": cid, "complaint": "real job"}, headers=tenant)).json()
    gone = (await client.post("/api/orders", json={"client_id": cid, "complaint": "OPENED IN ERROR"}, headers=tenant)).json()
    r = await client.post(f"/api/orders/{gone['id']}/archive", json={"reason": "wrong client", "refund": False}, headers=tenant)
    assert r.status_code == 200, r.text
    tok = await _portal(client, tenant, cid)
    p = (await client.get(f"/api/public/portal/{tok}")).json()
    complaints = [w["complaint"] for w in p["work_orders"]]
    assert complaints == ["real job"], complaints
    assert p["counts"]["work_orders"] == 1
    assert (await client.get(f"/api/public/portal/{tok}/order/{gone['id']}")).status_code == 404
    assert (await client.get(f"/api/public/portal/{tok}/order/{keep['id']}")).status_code == 200


@pytest.mark.asyncio
async def test_qa7_track_link_of_archived_job_is_gone(client, tenant, db):
    cid = (await client.post("/api/clients", json={"name": "Pat Doe", "phone": "5550001111"}, headers=tenant)).json()["id"]
    o = (await client.post("/api/orders", json={"client_id": cid, "complaint": "x"}, headers=tenant)).json()
    order = await db.get(Order, o["id"])
    if not order.public_token:
        order.public_token = uuid.uuid4().hex
        await db.commit()
    tok = order.public_token
    assert (await client.get(f"/api/public/track/{tok}")).status_code == 200
    r = await client.post(f"/api/orders/{o['id']}/archive", json={"reason": "dup", "refund": False}, headers=tenant)
    assert r.status_code == 200, r.text
    assert (await client.get(f"/api/public/track/{tok}")).status_code == 404


@pytest.mark.asyncio
async def test_qa7_portal_is_paged(client, tenant):
    cid = (await client.post("/api/clients", json={"name": "Pat Doe", "phone": "5550001111"}, headers=tenant)).json()["id"]
    for i in range(3):
        await client.post("/api/orders", json={"client_id": cid, "complaint": f"job {i}"}, headers=tenant)
    tok = await _portal(client, tenant, cid)
    p = (await client.get(f"/api/public/portal/{tok}?limit=2")).json()
    assert len(p["work_orders"]) == 2 and p["has_more"] is True and p["counts"]["work_orders"] == 3
    p2 = (await client.get(f"/api/public/portal/{tok}?limit=2&skip=2")).json()
    assert len(p2["work_orders"]) == 1 and p2["has_more"] is False
    assert (await client.get(f"/api/public/portal/{tok}?limit=1000")).status_code == 422


@pytest.mark.asyncio
async def test_qa7_portal_query_count_is_flat(client, tenant, db):
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    org = await db.get(Organisation, me["org_id"])
    org.oe_wo_allow_approval = True                  # exercise the per-job approval check too
    await db.commit()
    cid = (await client.post("/api/clients", json={"name": "Pat Doe", "phone": "5550001111"}, headers=tenant)).json()["id"]
    tok = await _portal(client, tenant, cid)
    engine = db.bind.sync_engine
    seen = []

    def count(*_a, **_k):
        seen.append(1)

    async def add(n):
        for i in range(n):
            o = (await client.post("/api/orders", json={"client_id": cid, "complaint": f"j{i}"}, headers=tenant)).json()
            db.add(Payment(org_id=me["org_id"], order_id=o["id"], client_id=cid, method="cash", kind="payment", amount=1))
        await db.commit()

    async def measure():
        seen.clear()
        event.listen(engine, "before_cursor_execute", count)
        try:
            r = await client.get(f"/api/public/portal/{tok}")
        finally:
            event.remove(engine, "before_cursor_execute", count)
        assert r.status_code == 200, r.text
        return len(seen), r.json()

    await add(2)
    small, d1 = await measure()
    await add(8)
    big, d2 = await measure()
    assert len(d2["work_orders"]) == 10
    assert all(w["amount_paid"] == 1 for w in d2["work_orders"])
    assert small == big, (small, big)


@pytest.mark.asyncio
async def test_qa7_batched_paid_fields_match_the_per_job_rule(client, tenant, db):
    """The portal's batched money figures equal order_money's one-job definition, credit notes
    on a shared invoice included."""
    from backend.models import Invoice, InvoiceOrder
    from backend.routers.public import _paid_fields, _paid_fields_many
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    org_id = me["org_id"]
    cid = (await client.post("/api/clients", json={"name": "Pat", "phone": "5550001111"}, headers=tenant)).json()["id"]
    ids = [(await client.post("/api/orders", json={"client_id": cid, "complaint": f"j{i}"}, headers=tenant)).json()["id"]
           for i in range(3)]
    orders = [await db.get(Order, i) for i in ids]
    for o, total in zip(orders, (100, 50, 30)):
        o.total = total
    db.add_all([
        Payment(org_id=org_id, order_id=ids[0], client_id=cid, method="cash", kind="payment", amount=40),
        Payment(org_id=org_id, order_id=ids[0], client_id=cid, method="cash", kind="refund", amount=5),
        Payment(org_id=org_id, order_id=ids[1], client_id=cid, method="card", kind="prepayment", amount=50),
    ])
    inv = Invoice(org_id=org_id, invoice_number=f"INV-{uuid.uuid4().hex[:5]}", order_id=ids[0], client_id=cid,
                  credited=15, status="issued")
    db.add(inv)
    await db.flush()
    db.add(InvoiceOrder(org_id=org_id, invoice_id=inv.id, order_id=ids[2]))
    await db.commit()
    fx = {i: 1 for i in ids}
    batched = await _paid_fields_many(db, orders, fx)
    for o in orders:
        assert batched[o.id] == await _paid_fields(db, o, 1), o.id
