"""Re-review E1 (inventory N1, N3, N4, N5): warehouse reach is per warehouse, free-standing
returns take back only sold units, merges respect reach, and an unwind outside reach says where.

- N1: a phone still allocated to an open job was accepted on a free-standing return (no job), so
  the front desk could put a phantom unit on the shelf (on-hand 3 with only 2 phones).
- N3: `warehouses.reach` added a listed warehouse's whole location, so a role limited to the shop
  floor (W1) could draw and see the secure stock (W2) at the same location.
- N4: product merge folded every location's stock with no reach check.
- N5: removing a line from a job moved to another branch sends its part back to the branch it came
  from; for someone who can't reach that branch it is refused, with a sentence naming the branch."""
import pytest
from sqlalchemy import func, select

from backend.tests import kit_helpers as K
from backend.tests.test_inventory_bins_passport_stocktake import _add_product_line, _order
from backend.tests.test_qa_f1_jobs import _staff
from backend.tests.test_qa_f2_stock import _on_hand, _phone
from backend.tests.test_scoping_crm_chat import _scoped_login

pytestmark = pytest.mark.asyncio


# ── N1 ─────────────────────────────────────────────────────────────────────────────────────────
async def test_free_standing_return_refuses_a_unit_still_on_an_open_job(client, tenant, db):
    from backend.models import SerializedItem
    p, loc, sns = await _phone(client, tenant, n=2)
    oid = await _order(client, tenant, loc)
    r = await _add_product_line(client, tenant, oid, p["id"], qty=1, serial_number=sns[0])
    assert r.status_code == 201, r.text
    line_id = r.json()["line_id"]
    desk = await _staff(client, tenant, {"can_create_returns": True, "can_manage_inventory": False})
    r = await client.post("/api/returns", headers=desk, json={
        "lines": [{"part_id": p["id"], "qty": 1, "serials": [sns[0]]}]})
    assert r.status_code == 409, r.text                       # was 201: phantom unit on the shelf
    assert "job" in r.json()["detail"].lower()
    assert await _on_hand(db, p["id"]) == 1
    # the job gives its unit back: 2 on the shelf, 2 phones in stock
    r = await client.delete(f"/api/orders/{oid}/lines/{line_id}", headers=tenant)
    assert r.status_code in (200, 204), r.text
    assert await _on_hand(db, p["id"]) == 2
    db.expire_all()
    n = (await db.execute(select(func.count()).select_from(SerializedItem).where(
        SerializedItem.part_id == p["id"], SerializedItem.status == "in_stock"))).scalar_one()
    assert n == 2


async def test_free_standing_return_still_takes_a_sold_unit(client, tenant, db):
    p, loc, sns = await _phone(client, tenant, n=1)
    r = await client.post("/api/sales", headers=tenant, json={"lines": [{
        "kind": "product", "ref_id": p["id"], "description": "iPhone 13", "qty": 1, "unit_price": 400,
        "serials": [sns[0]]}]})
    assert r.status_code in (200, 201), r.text
    assert await _on_hand(db, p["id"]) == 0
    desk = await _staff(client, tenant, {"can_create_returns": True, "can_manage_inventory": False})
    r = await client.post("/api/returns", headers=desk, json={
        "lines": [{"part_id": p["id"], "qty": 1, "serials": [sns[0]]}]})
    assert r.status_code == 201, r.text
    assert await _on_hand(db, p["id"]) == 1


# ── N3 ─────────────────────────────────────────────────────────────────────────────────────────
async def _two_warehouses(client, tenant, db):
    """Location L: the default product warehouse W1 holds 2, a second product warehouse W2 holds 3.
    Returns (loc, w1, w2, pid, H) where H is a person whose role lists W1 only."""
    from backend.models import StockLevel, Warehouse
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    loc = await K.loc(client, tenant)
    pid = await K.part(client, tenant, loc, "Screen", 99, 10, qty=2)
    db.expire_all()
    w1 = (await db.execute(select(StockLevel.warehouse_id).where(
        StockLevel.part_id == pid, StockLevel.location_id == loc))).scalar_one()
    assert w1
    w2 = Warehouse(org_id=me["org_id"], name="Secure cage", kind="product", scope="location",
                   location_id=loc, is_default=False)
    db.add(w2)
    await db.flush()
    db.add(StockLevel(org_id=me["org_id"], part_id=pid, location_id=loc, warehouse_id=w2.id,
                      qty_available=3, qty_reserved=0))
    await db.commit()
    H, _emp, _role = await _scoped_login(client, db, me["org_id"], email=f"w1-{pid[:6]}@x.dev",
                                         warehouse_scope="selected", warehouse_ids=[w1])
    return loc, w1, w2.id, pid, H


async def _in_wh(db, pid, wid):
    from backend.models import StockLevel
    db.expire_all()
    return float((await db.execute(select(func.coalesce(func.sum(StockLevel.qty_available), 0)).where(
        StockLevel.part_id == pid, StockLevel.warehouse_id == wid))).scalar_one())


async def test_role_limited_to_one_warehouse_cant_draw_the_other_warehouse_at_its_location(client, tenant, db):
    loc, w1, w2, pid, H = await _two_warehouses(client, tenant, db)
    oid, _ = await K.job(client, tenant, loc)
    r = await client.post(f"/api/orders/{oid}/lines", headers=H, json={
        "kind": "product", "line_type": "part", "ref_id": pid, "description": "Screen", "qty": 3, "unit_price": 99})
    assert r.status_code in (200, 201), r.text
    assert await _in_wh(db, pid, w2) == 3                    # was 2: the secure cage paid the shortfall
    assert await _in_wh(db, pid, w1) == 0


async def test_role_limited_to_one_warehouse_sees_only_that_warehouse(client, tenant, db):
    loc, w1, w2, pid, H = await _two_warehouses(client, tenant, db)
    stock = (await client.get(f"/api/inventory/parts/{pid}/stock", headers=H)).json()
    assert stock["total_available"] == 2, stock               # was 5
    assert (await client.get(f"/api/catalog/products/{pid}", headers=H)).json()["product"]["qty_available"] == 2
    # the owner still sees both
    assert (await client.get(f"/api/inventory/parts/{pid}/stock", headers=tenant)).json()["total_available"] == 5


async def test_location_listed_on_the_role_still_reaches_every_warehouse_there(client, tenant, db):
    from backend.services import warehouses as whs
    loc, w1, w2, pid, _H = await _two_warehouses(client, tenant, db)
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    H2, emp, _role = await _scoped_login(client, db, me["org_id"], email=f"loc-{pid[:6]}@x.dev",
                                         warehouse_scope="selected", warehouse_ids=[loc])
    stock = (await client.get(f"/api/inventory/parts/{pid}/stock", headers=H2)).json()
    assert stock["total_available"] == 5
    r = await whs.reach(db, emp)
    assert whs.reachable(r, loc, w2) and whs.reachable(r, loc, w1)


async def _login_with(client, db, org_id, email, perms, **role_kw):
    from backend.models import Employee, Role
    from backend.services.auth import hash_password
    role = Role(org_id=org_id, name="Scoped mgr", permissions=perms, perms_explicit=True, **role_kw)
    db.add(role)
    await db.flush()
    db.add(Employee(org_id=org_id, name="Scoped mgr", email=email, password_hash=hash_password("pw-123456789"),
                    active=True, role_id=role.id))
    await db.commit()
    tok = (await client.post("/api/auth/token", data={"username": email, "password": "pw-123456789"})).json()
    return {"Authorization": f"Bearer {tok['access_token']}"}


# ── N4 ─────────────────────────────────────────────────────────────────────────────────────────
async def test_merge_refuses_products_with_stock_out_of_reach(client, tenant, db):
    from backend.models import Location
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    main = await K.loc(client, tenant)
    b = Location(org_id=me["org_id"], name="Branch B")
    db.add(b)
    await db.commit()
    await db.refresh(b)
    keep = await K.part(client, tenant, main, "Screen A", 99, 10, qty=0)
    dupe = await K.part(client, tenant, main, "Screen A dup", 99, 10, qty=4)       # 4 at main only
    H = await _login_with(client, db, me["org_id"], f"mg-{keep[:6]}@x.dev", {"can_manage_inventory": True},
                          warehouse_scope="selected", warehouse_ids=[b.id])
    r = await client.post("/api/inventory/parts/merge", headers=H, json={"target_id": keep, "source_ids": [dupe]})
    assert r.status_code == 403, r.text                       # was 200: re-pointed main's stock
    assert "access" in r.json()["detail"]
    assert await _on_hand(db, dupe) == 4
    # the owner can merge
    r = await client.post("/api/inventory/parts/merge", headers=tenant, json={"target_id": keep, "source_ids": [dupe]})
    assert r.status_code == 200, r.text
    assert await _on_hand(db, keep) == 4


# ── N5 ─────────────────────────────────────────────────────────────────────────────────────────
async def test_unwinding_a_line_of_a_moved_job_outside_reach_names_the_branch(client, tenant, db):
    """Decision: the part goes back to the shelf it came from (the branch on the job's "use"
    movement), never to the job's new branch. Someone who can't reach that shelf gets a 403 that
    names it, and nothing changes. Someone who can (the owner, a manager there) removes it."""
    from backend.models import Location
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    main = await K.loc(client, tenant)
    b = Location(org_id=me["org_id"], name="Branch B")
    db.add(b)
    await db.commit()
    await db.refresh(b)
    pid = await K.part(client, tenant, main, "Screen", 99, 10, qty=5)
    oid, _ = await K.job(client, tenant, main)
    r = await client.post(f"/api/orders/{oid}/lines", headers=tenant, json={
        "kind": "product", "line_type": "part", "ref_id": pid, "description": "Screen", "qty": 1, "unit_price": 99})
    assert r.status_code in (200, 201), r.text
    line_id = r.json()["line_id"]
    r = await client.post(f"/api/orders/{oid}/transfer", headers=tenant, json={"location_id": b.id})
    assert r.status_code == 200, r.text
    main_name = (await db.execute(select(Location.name).where(Location.id == main))).scalar_one()
    H, _emp, _role = await _scoped_login(client, db, me["org_id"], email=f"n5-{pid[:6]}@x.dev",
                                         warehouse_scope="selected", warehouse_ids=[b.id])
    r = await client.delete(f"/api/orders/{oid}/lines/{line_id}", headers=H)
    assert r.status_code == 403, r.text
    assert main_name in r.json()["detail"], r.json()
    assert await _on_hand(db, pid) == 4
    assert any(x["id"] == line_id for x in await K.lines(client, tenant, oid))
    r = await client.delete(f"/api/orders/{oid}/lines/{line_id}", headers=tenant)
    assert r.status_code in (200, 204), r.text
    assert await _on_hand(db, pid) == 5
