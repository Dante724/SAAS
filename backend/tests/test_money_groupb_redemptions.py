"""Re-review money N2 (archive/delete job keeps redemptions), N5 (coupon give-back uncapped) and
N8 (points give-back with a changed client; returned points counted toward the tier)."""
from sqlalchemy import select, update

from backend.models import Coupon, LoyaltyLedger, OrderLine, Organisation
from backend.tests.test_p1_money_audit import _acct, _client, _job, _me, _order


async def test_archiving_a_job_gives_back_gift_card_coupon_and_points(client, tenant, db):
    me = await _me(client, tenant)
    await db.execute(update(Organisation).where(Organisation.id == me["org_id"]).values(loyalty_point_value=1.0))
    await db.commit()
    till = await _acct(client, tenant, "Till")
    cid = await _client(client, tenant)
    db.add(LoyaltyLedger(org_id=me["org_id"], client_id=cid, points=100, ref_type="manual", note="seed"))
    await db.commit()
    code = (await client.post("/api/giftcards", json={"value": 30, "client_id": cid}, headers=tenant)).json()["code"]
    assert (await client.post("/api/coupons", json={"code": "ARCH5", "kind": "fixed", "value": 5, "max_uses": 1},
                              headers=tenant)).status_code == 201
    oid = await _job(client, tenant, cid, price=100)
    assert (await client.post(f"/api/orders/{oid}/redeem-giftcard", json={"code": code},
                              headers=tenant)).status_code == 200
    assert (await client.post(f"/api/orders/{oid}/apply-coupon", json={"code": "ARCH5"},
                              headers=tenant)).status_code == 200
    assert (await client.post(f"/api/orders/{oid}/redeem-loyalty", json={"points": 20},
                              headers=tenant)).status_code == 200
    r = await client.post(f"/api/cash/orders/{oid}/pay", json={"method": "cash", "account_id": till, "amount": 45},
                          headers=tenant)
    assert r.status_code == 200, r.text

    r = await client.post(f"/api/orders/{oid}/archive", json={"reason": "customer cancelled", "refund": True},
                          headers=tenant)
    assert r.status_code == 200, r.text
    assert r.json()["refunded"] == 45
    assert (await client.get(f"/api/giftcards/{code}", headers=tenant)).json()["balance"] == 30
    db.expire_all()
    assert (await db.execute(select(Coupon.uses).where(Coupon.code == "ARCH5"))).scalar_one() == 0
    assert (await client.get(f"/api/clients/{cid}/loyalty", headers=tenant)).json()["points"] == 100
    # the lines that spent them are gone, so a restore can't keep the discount for free
    rows = (await db.execute(select(OrderLine.description).where(OrderLine.order_id == oid))).scalars().all()
    assert rows == ["Repair"], rows


async def test_coupon_use_is_not_returned_for_a_line_the_coupon_never_made(client, tenant, db):
    assert (await client.post("/api/coupons", json={"code": "CAP1", "kind": "fixed", "value": 5, "max_uses": 3},
                              headers=tenant)).status_code == 201
    cid = await _client(client, tenant)
    a = await _job(client, tenant, cid)
    assert (await client.post(f"/api/orders/{a}/apply-coupon", json={"code": "CAP1"},
                              headers=tenant)).status_code == 200
    # a stray negative fee line that only *says* it is this coupon (not made by apply-coupon)
    b = await _job(client, tenant, cid)
    db.add(OrderLine(order_id=b, line_type="fee", kind="custom", description="Coupon CAP1", qty=1,
                     unit_price=-5, total=-5))
    await db.commit()
    stray = next(ln for ln in (await _order(client, tenant, b))["lines"] if ln["description"] == "Coupon CAP1")
    r = await client.delete(f"/api/orders/{b}/lines/{stray['id']}", headers=tenant)
    assert r.status_code == 200, r.text
    db.expire_all()
    assert (await db.execute(select(Coupon.uses).where(Coupon.code == "CAP1"))).scalar_one() == 1


async def test_points_go_back_to_the_client_who_spent_them_after_a_client_change(client, tenant, db):
    me = await _me(client, tenant)
    await db.execute(update(Organisation).where(Organisation.id == me["org_id"]).values(loyalty_point_value=1.0))
    await db.commit()
    first = await _client(client, tenant, "First")
    second = await _client(client, tenant, "Second")
    db.add(LoyaltyLedger(org_id=me["org_id"], client_id=first, points=50, ref_type="manual", note="seed"))
    await db.commit()
    oid = await _job(client, tenant, first)
    assert (await client.post(f"/api/orders/{oid}/redeem-loyalty", json={"points": 20},
                              headers=tenant)).status_code == 200
    from backend.models import Order
    await db.execute(update(Order).where(Order.id == oid).values(client_id=second))
    await db.commit()
    line = next(ln for ln in (await _order(client, tenant, oid))["lines"]
                if ln["description"].startswith("Loyalty points"))
    assert (await client.delete(f"/api/orders/{oid}/lines/{line['id']}", headers=tenant)).status_code == 200
    assert (await client.get(f"/api/clients/{first}/loyalty", headers=tenant)).json()["points"] == 50
    assert (await client.get(f"/api/clients/{second}/loyalty", headers=tenant)).json()["points"] == 0


async def test_returned_points_do_not_count_toward_the_tier(client, tenant, db):
    me = await _me(client, tenant)
    cid = await _client(client, tenant)
    db.add(LoyaltyLedger(org_id=me["org_id"], client_id=cid, points=100, ref_type="manual", note="seed"))
    db.add(LoyaltyLedger(org_id=me["org_id"], client_id=cid, points=-40, ref_type="order", ref_id="o1",
                         note="Redeemed on WO-1"))
    db.add(LoyaltyLedger(org_id=me["org_id"], client_id=cid, points=40, ref_type="order", ref_id="o1",
                         note="Returned: Removed from WO-1"))
    await db.commit()
    from backend.services.payments import lifetime_earned_points
    assert await lifetime_earned_points(db, me["org_id"], cid) == 100


async def test_membership_banner_read_takes_no_plan_lock(client, tenant, db, monkeypatch):
    """Re-review money N6: GET /memberships/order/{id} (the job banner) ran lock_memberships, a
    FOR UPDATE on Postgres and a write lock on SQLite, so a banner refresh contended with closes."""
    from backend.services import memberships as msvc
    oil = (await client.post("/api/catalog/services", json={"name": "Oil", "price": 80}, headers=tenant)).json()["id"]
    plan = (await client.post("/api/memberships/plans", json={
        "name": "Care", "price_yearly": 100, "items": [{"kind": "service", "service_id": oil, "qty": 1}]},
        headers=tenant)).json()
    cid = await _client(client, tenant)
    boat = (await client.post("/api/assets", json={"owner_type": "client", "client_id": cid, "brand": "B",
                                                   "model": "M"}, headers=tenant)).json()["id"]
    r = await client.post("/api/memberships", json={"plan_id": plan["id"], "client_id": cid, "device_id": boat,
                                                    "billing": "yearly"}, headers=tenant)
    assert r.status_code in (200, 201), r.text
    oid = (await client.post("/api/orders", json={"client_id": cid, "device_id": boat, "complaint": "x"},
                             headers=tenant)).json()["id"]
    # a line the plan would cover, written directly so the plan isn't applied to it yet
    db.add(OrderLine(order_id=oid, line_type="labour", kind="service", ref_id=oil, description="Oil",
                     qty=1, unit_price=80, total=80))
    await db.commit()
    locked = []

    async def spy(db_, ids):
        locked.extend(ids)
    monkeypatch.setattr(msvc, "lock_memberships", spy)
    r = await client.get(f"/api/memberships/order/{oid}", headers=tenant)
    assert r.status_code == 200, r.text
    assert r.json()["coverable"] == 1, r.json()
    assert locked == []
