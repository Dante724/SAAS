"""Build 144ce — platform-admin portal API: separate auth, tenant lifecycle,
per-employee windows, coupons, and expiry enforcement."""
import pytest

from backend.tests.admin_helpers import admin_headers


async def _admin_hdr(client):
    return (await admin_headers(client))[0]


async def _tenant(client, org="Acme", email="o@acme.dev"):
    r = await client.post("/api/auth/register", json={
        "org_name": org, "name": "Owner", "email": email, "password": "pw-12345678"})
    assert r.status_code == 201
    return r.json()["org_id"]


@pytest.mark.asyncio
async def test_admin_login_and_scope_isolation(client):
    hdr = await _admin_hdr(client)
    assert (await client.get("/api/admin/me", headers=hdr)).status_code == 200
    # a tenant token must never satisfy a platform route
    await _tenant(client, "ScopeCo", "scope@acme.dev")
    tt = await client.post("/api/auth/token", data={"username": "scope@acme.dev", "password": "pw-12345678"})
    thdr = {"Authorization": f"Bearer {tt.json()['access_token']}"}
    assert (await client.get("/api/admin/stats", headers=thdr)).status_code == 401
    # and no token at all is rejected
    assert (await client.get("/api/admin/stats")).status_code == 401


@pytest.mark.asyncio
async def test_new_tenant_lands_in_new_segment(client):
    hdr = await _admin_hdr(client)
    await _tenant(client, "FreshCo", "fresh@acme.dev")
    stats = await client.get("/api/admin/stats", headers=hdr)
    assert stats.json()["segments"]["new"] >= 1
    lst = await client.get("/api/admin/tenants?segment=new", headers=hdr)
    assert any(t["name"] == "FreshCo" for t in lst.json()["items"])


@pytest.mark.asyncio
async def test_patch_tenant_term_and_status(client):
    hdr = await _admin_hdr(client)
    oid = await _tenant(client, "TermCo", "term@acme.dev")
    r = await client.patch(f"/api/admin/tenants/{oid}",
                           json={"sub_status": "active", "sub_end": "2030-12-31", "reason": "Signed up"}, headers=hdr)
    assert r.status_code == 200
    r = await client.post(f"/api/admin/tenants/{oid}/discount", json={"pct": 15, "reason": "Launch deal"}, headers=hdr)
    assert r.status_code == 200
    d = await client.get(f"/api/admin/tenants/{oid}", headers=hdr)
    body = d.json()
    assert body["segment"] == "paid" and body["sub_end"] == "2030-12-31" and body["discount_pct"] == 15


@pytest.mark.asyncio
async def test_bad_sub_status_rejected(client):
    hdr = await _admin_hdr(client)
    oid = await _tenant(client, "BadCo", "bad@acme.dev")
    assert (await client.patch(f"/api/admin/tenants/{oid}",
            json={"sub_status": "nonsense", "reason": "test"}, headers=hdr)).status_code == 422


@pytest.mark.asyncio
async def test_expired_term_blocks_tenant_login(client):
    hdr = await _admin_hdr(client)
    oid = await _tenant(client, "ExpCo", "exp@acme.dev")
    await client.patch(f"/api/admin/tenants/{oid}", json={"sub_end": "2020-01-01", "reason": "Ended"}, headers=hdr)
    r = await client.post("/api/auth/token", data={"username": "exp@acme.dev", "password": "pw-12345678"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_suspended_blocks_tenant_login(client):
    hdr = await _admin_hdr(client)
    oid = await _tenant(client, "SuspCo", "susp@acme.dev")
    await client.post(f"/api/admin/tenants/{oid}/suspend", json={"reason": "Unpaid"}, headers=hdr)
    r = await client.post("/api/auth/token", data={"username": "susp@acme.dev", "password": "pw-12345678"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_employee_access_window_blocks_login(client):
    hdr = await _admin_hdr(client)
    oid = await _tenant(client, "WinCo", "win@acme.dev")
    detail = await client.get(f"/api/admin/tenants/{oid}", headers=hdr)
    emp_id = detail.json()["employees_detail"][0]["id"]
    await client.patch(f"/api/admin/tenants/{oid}/employees/{emp_id}",
                       json={"access_end": "2020-01-01", "reason": "Left the shop"}, headers=hdr)
    r = await client.post("/api/auth/token", data={"username": "win@acme.dev", "password": "pw-12345678"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_coupon_create_and_apply(client):
    hdr = await _admin_hdr(client)
    oid = await _tenant(client, "CoupCo", "coup@acme.dev")
    c = await client.post("/api/admin/coupons", json={"code": "SAVE20", "percent": 20}, headers=hdr)
    assert c.status_code == 201
    ap = await client.post(f"/api/admin/tenants/{oid}/apply-coupon", json={"code": "SAVE20"}, headers=hdr)
    assert ap.status_code == 200 and ap.json()["discount_pct"] == 20


@pytest.mark.asyncio
async def test_coupon_plan_scope_enforced(client):
    hdr = await _admin_hdr(client)
    oid = await _tenant(client, "PlanCo", "plan@acme.dev")  # plan defaults to 'pro'
    made = await client.post("/api/admin/coupons", json={"code": "SOLOONLY", "percent": 30, "plan": "solo"}, headers=hdr)
    assert made.status_code == 201, made.text
    ap = await client.post(f"/api/admin/tenants/{oid}/apply-coupon", json={"code": "SOLOONLY"}, headers=hdr)
    assert ap.status_code == 400 and "solo plan" in ap.json()["detail"]
