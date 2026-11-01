"""Wire-through pass: the four stored-but-inert settings now actually enforce.
Each test proves OFF = no behavior change and ON = enforced."""
import pytest
from sqlalchemy import select

from backend.services.dates import now_utc


async def _loc(client, tenant):
    return (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]


async def _client(client, tenant, name="C"):
    return (await client.post("/api/clients", json={"name": name}, headers=tenant)).json()["id"]


# ── Item 1: asset_one_open_ticket ────────────────────────────────────────────
@pytest.mark.asyncio
async def test_asset_one_open_ticket(client, tenant):
    loc = await _loc(client, tenant)
    cid = await _client(client, tenant, "Asset Owner")
    body = {"location_id": loc, "client_id": cid, "complaint": "fix",
            "asset_brand": "Bosch", "asset_model": "GSR", "asset_serial": "SN-1"}
    o1 = await client.post("/api/orders", json=body, headers=tenant)
    assert o1.status_code in (200, 201), o1.text
    dev_id = (await client.get(f"/api/orders/{o1.json()['id']}", headers=tenant)).json()["device"]["id"]

    # OFF (default): a second open ticket on the same device is allowed
    o2 = await client.post("/api/orders", json={"location_id": loc, "client_id": cid,
                                                "complaint": "again", "device_id": dev_id}, headers=tenant)
    assert o2.status_code in (200, 201), "off-by-default must allow"

    # ON: now the same device on a new open ticket is blocked
    await client.put("/api/staff/company", json={"asset_one_open_ticket": True}, headers=tenant)
    o3 = await client.post("/api/orders", json={"location_id": loc, "client_id": cid,
                                                "complaint": "third", "device_id": dev_id}, headers=tenant)
    assert o3.status_code == 409 and "open ticket" in o3.json()["detail"]


# ── Item 2: role location scope ──────────────────────────────────────────────
@pytest.mark.asyncio
async def test_role_location_scope_enforced(client, tenant, db):
    from backend.models import Employee, Role
    from backend.services.auth import create_access_token, hash_password

    me = (await client.get("/api/auth/me", headers=tenant)).json()
    locs = (await client.get("/api/staff/locations", headers=tenant)).json()["items"]
    loc_a = locs[0]["id"]

    # a role scoped to a DIFFERENT location than loc_a
    role = Role(org_id=me["org_id"], name="Loc-B only", permissions={},
                location_scope="selected", location_ids='["some-other-loc"]')
    db.add(role)
    await db.flush()
    emp = Employee(org_id=me["org_id"], name="Scoped", email="scoped@x.dev",
                   password_hash=hash_password("pw-123456789"), active=True,
                   role_id=role.id, token_version=0)
    db.add(emp)
    await db.commit()
    await db.refresh(emp)

    tok = create_access_token({"sub": emp.id, "org_id": me["org_id"], "tv": 0})
    hdr = {"Authorization": f"Bearer {tok}"}
    # creating an order at loc_a (out of scope) is forbidden
    r = await client.post("/api/orders", json={"location_id": loc_a, "complaint": "x"}, headers=hdr)
    assert r.status_code == 403 and "location" in r.json()["detail"].lower()

    # widen the role to include loc_a -> allowed
    role.location_ids = f'["{loc_a}"]'
    await db.commit()
    ok = await client.post("/api/orders", json={"location_id": loc_a, "complaint": "x"}, headers=hdr)
    assert ok.status_code in (200, 201), ok.text


@pytest.mark.asyncio
async def test_unscoped_role_unaffected(client, tenant):
    """Owner (no role) is never location-restricted — the default path is untouched."""
    loc = await _loc(client, tenant)
    r = await client.post("/api/orders", json={"location_id": loc, "complaint": "x"}, headers=tenant)
    assert r.status_code in (200, 201)


# ── Item 3: rounding ─────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_rounding_applied_when_enabled(client, tenant):
    loc = await _loc(client, tenant)
    o = (await client.post("/api/orders", json={"location_id": loc, "complaint": "x"}, headers=tenant)).json()
    # a line total of 10.02 -> nearest 0.05 = 10.00; 10.03 -> 10.05
    await client.post(f"/api/orders/{o['id']}/lines", json={
        "line_type": "part", "kind": "custom", "description": "p", "qty": 1, "unit_price": 10.02}, headers=tenant)
    off = (await client.get(f"/api/orders/{o['id']}", headers=tenant)).json()["total"]
    assert off == pytest.approx(10.02), "off-by-default = exact total"

    await client.put("/api/staff/company", json={"rounding_enabled": True}, headers=tenant)
    # re-trigger recalc by adding a zero line
    await client.post(f"/api/orders/{o['id']}/lines", json={
        "line_type": "fee", "kind": "custom", "description": "z", "qty": 1, "unit_price": 0.0}, headers=tenant)
    on = (await client.get(f"/api/orders/{o['id']}", headers=tenant)).json()["total"]
    assert on == pytest.approx(10.00), f"rounded to nearest 0.05, got {on}"


# ── Item 4: warranty_count_from ──────────────────────────────────────────────
@pytest.mark.asyncio
async def test_warranty_count_from(client, tenant, db):
    from datetime import datetime, timedelta

    from backend.models import Order

    loc = await _loc(client, tenant)
    term = await client.post("/api/statuses", json={
        "name": "Closed WC", "key": "closed-wc", "status_group": "closed", "is_terminal": True}, headers=tenant)
    assert term.status_code == 201

    async def _make_closed_order():
        o = (await client.post("/api/orders", json={"location_id": loc, "complaint": "x"}, headers=tenant)).json()
        # backdate created_at 100 days
        row = (await db.execute(select(Order).where(Order.id == o["id"]))).scalar_one()
        row.created_at = now_utc() - timedelta(days=100)
        await db.commit()
        await client.post(f"/api/orders/{o['id']}/lines", json={
            "line_type": "labour", "kind": "custom", "description": "job",
            "qty": 1, "unit_price": 50, "warranty_days": 30}, headers=tenant)
        await client.patch(f"/api/orders/{o['id']}/status", json={"status": "closed-wc"}, headers=tenant)
        db.expire_all()
        return (await db.execute(select(Order).where(Order.id == o["id"]))).scalar_one()

    # default 'closure': warranty ~ now + 30d (future)
    await client.put("/api/staff/company", json={"warranty_count_from": "closure"}, headers=tenant)
    c_order = await _make_closed_order()
    assert c_order.warranty_until is not None
    assert c_order.warranty_until > (now_utc() + timedelta(days=20)).date()

    # 'created': warranty = created(-100d) + 30d => already in the PAST
    await client.put("/api/staff/company", json={"warranty_count_from": "created"}, headers=tenant)
    cr_order = await _make_closed_order()
    assert cr_order.warranty_until is not None
    assert cr_order.warranty_until < now_utc().date()
