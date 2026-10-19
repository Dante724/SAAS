"""Deleting a sale or a refund ("Delete sales" / "Delete refunds") voids it: the document stays
on record and every money and stock movement it made is put back.

- A sale void returns products to the shelf and to the FIFO cost layers they came from, gives
  back the hold it took from another job, the coupon use, the gift-card spend and the loyalty
  points, and takes the takings back out of the cashbox.
- A refund void takes the payout back out of the cashbox ledger, removes the refund Payment (the
  order is paid again), reverses the client ledger entry and gives back the commission it clawed
  back, as a positive entry in the same accrual group.
"""
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select


async def _register(client):
    email = f"vd-{uuid.uuid4().hex[:8]}@t.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Co", "name": "Owner", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    body = r.json()
    return {"Authorization": f"Bearer {body['access_token']}"}, body["org_id"]


async def _teammate(client, H, deny):
    role = (await client.post("/api/settings/rbac/roles", json={
        "name": f"No {deny}", "permissions": {deny: False}}, headers=H)).json()["id"]
    email = f"tm-{uuid.uuid4().hex[:8]}@t.dev"
    r = await client.post("/api/staff/employees", json={
        "name": "Clerk", "email": email, "password": "pw-test-12345", "role_id": role}, headers=H)
    assert r.status_code == 201, r.text
    tok = await client.post("/api/auth/token", data={"username": email, "password": "pw-test-12345"})
    return {"Authorization": f"Bearer {tok.json()['access_token']}"}


async def _till(client, H):
    r = await client.post("/api/cash/accounts", json={"name": "Till", "payment_option": "cash"}, headers=H)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _account_balance(client, H, aid):
    return next(a for a in (await client.get("/api/cash/accounts", headers=H)).json()["items"]
                if a["id"] == aid)["balance"]


async def _loc(client, H):
    return (await client.get("/api/staff/locations", headers=H)).json()["items"][0]["id"]


# ── sales ─────────────────────────────────────────────────────────

async def _stocked_part(client, H, db, org, loc):
    """10 on the shelf: an older layer of 3 at 10 and a newer one of 7 at 20, 2 held for a job."""
    from backend.models import StockItemBatch, StockLevel, StockReservation
    pid = (await client.post("/api/inventory/parts", json={"name": "Cell", "sku": f"C-{uuid.uuid4().hex[:4]}",
                                                         "sell_price": 30}, headers=H)).json()["id"]
    job = (await client.post("/api/orders", json={"location_id": loc, "complaint": "job"}, headers=H)).json()["id"]
    sl = (await db.execute(select(StockLevel).where(StockLevel.part_id == pid,
                                                    StockLevel.location_id == loc))).scalar_one_or_none()
    if sl is None:
        sl = StockLevel(part_id=pid, location_id=loc)
        db.add(sl)
    sl.qty_available, sl.qty_reserved = 10, 2
    now = datetime.utcnow()
    old = StockItemBatch(org_id=org, part_id=pid, location_id=loc, unit_cost=10, qty_received=3,
                         qty_remaining=3, received_at=now - timedelta(days=2))
    new = StockItemBatch(org_id=org, part_id=pid, location_id=loc, unit_cost=20, qty_received=7,
                         qty_remaining=7, received_at=now - timedelta(days=1))
    hold = StockReservation(org_id=org, part_id=pid, location_id=loc, order_id=job, qty=2)
    db.add_all([old, new, hold])
    await db.commit()
    return pid, old.id, new.id, hold.id


async def _stock_state(db, pid, loc, old_id, new_id, hold_id):
    from backend.models import StockItemBatch, StockLevel, StockReservation
    db.expire_all()
    sl = (await db.execute(select(StockLevel).where(StockLevel.part_id == pid,
                                                    StockLevel.location_id == loc))).scalar_one()
    layers = [float((await db.execute(select(StockItemBatch.qty_remaining).where(StockItemBatch.id == i))).scalar_one())
              for i in (old_id, new_id)]
    hold = (await db.execute(select(StockReservation).where(StockReservation.id == hold_id))).scalar_one()
    return float(sl.qty_available), float(sl.qty_reserved), layers, float(hold.qty), bool(hold.released)


@pytest.mark.asyncio
async def test_sale_void_puts_back_stock_layers_holds_rewards_and_cash(client, db):
    from backend.models import Client, Coupon, GiftCard, LoyaltyLedger, Organisation, Transaction
    H, org = await _register(client)
    loc = await _loc(client, H)
    till = await _till(client, H)
    pid, old_id, new_id, hold_id = await _stocked_part(client, H, db, org, loc)

    o = (await db.execute(select(Organisation).where(Organisation.id == org))).scalar_one()
    o.loyalty_point_value = 0.05
    cl = Client(org_id=org, name="Pat")
    db.add(cl)
    await db.flush()
    cid = cl.id
    db.add(LoyaltyLedger(org_id=org, client_id=cid, points=400, ref_type="seed", note="t"))
    await db.commit()
    await client.post("/api/coupons", json={"code": "VOID10", "kind": "pct", "value": 10, "min_total": 0,
                                            "max_uses": 5}, headers=H)
    card = (await client.post("/api/giftcards", json={"value": 20, "client_id": cid}, headers=H)).json()

    r = await client.post("/api/sales", json={
        "client_id": cid, "account_id": till, "coupon_code": "void10", "gift_card_code": card["code"],
        "redeem_points": 100, "lines": [
            {"kind": "product", "ref_id": pid, "description": "Cell", "qty": 9, "unit_price": 30},
            {"kind": "service", "description": "Fit", "qty": 1, "unit_price": 100}]}, headers=H)
    assert r.status_code == 201, r.text
    sale = r.json()
    assert sale["total"] == pytest.approx(370 - 37 - 20 - 5)
    # the sale took the whole older layer, 6 of the newer, and 1 unit held for the job (override)
    assert await _stock_state(db, pid, loc, old_id, new_id, hold_id) == (1, 1, [0, 1], 1, False)
    assert await _account_balance(client, H, till) == pytest.approx(sale["total"])

    T = await _teammate(client, H, "can_delete_sales")
    assert (await client.delete(f"/api/sales/{sale['id']}", headers=T)).status_code == 403

    r = await client.delete(f"/api/sales/{sale['id']}?reason=rang+up+twice", headers=H)
    assert r.status_code == 200, r.text
    assert await _stock_state(db, pid, loc, old_id, new_id, hold_id) == (10, 2, [3, 7], 2, False)
    assert await _account_balance(client, H, till) == pytest.approx(0)
    db.expire_all()
    leg = (await db.execute(select(Transaction).where(Transaction.ref_type == "sale",
                                                      Transaction.ref_id == sale["id"]))).scalar_one()
    assert leg.status == "deleted" and leg.void_reason == "rang up twice"
    assert (await db.execute(select(Coupon.uses).where(Coupon.org_id == org, Coupon.code == "VOID10"))).scalar_one() == 0
    assert float((await db.execute(select(GiftCard.balance).where(GiftCard.code == card["code"]))).scalar_one()) == 20
    pts = (await db.execute(select(func.sum(LoyaltyLedger.points)).where(LoyaltyLedger.client_id == cid))).scalar_one()
    assert float(pts) == 400

    listed = next(s for s in (await client.get("/api/sales", headers=H)).json()["items"] if s["id"] == sale["id"])
    assert listed["voided_at"] and listed["void_reason"] == "rang up twice"
    assert (await client.delete(f"/api/sales/{sale['id']}", headers=H)).status_code == 409   # once only


# ── refunds ───────────────────────────────────────────────────────

async def _paid_closed_order(client, H, db, org, till):
    """A 500 labour job, paid in full and closed, earning its technician 10% (50)."""
    from backend.models import CommissionRule
    tech = (await client.post("/api/staff/employees", json={
        "name": "Tech", "email": f"tc-{uuid.uuid4().hex[:8]}@t.dev", "password": "pw-test-12345"}, headers=H)).json()["id"]
    db.add(CommissionRule(org_id=org, name="Labour 10", event="labor_performed", calc_type="percent", amount=10))
    await db.commit()
    key = f"done-{uuid.uuid4().hex[:6]}"
    await client.post("/api/statuses", json={"name": key, "key": key, "status_group": "closed",
                                             "is_terminal": True}, headers=H)
    cl = (await client.post("/api/clients", json={"name": "Ravi", "phone": "+15551234"}, headers=H)).json()["id"]
    o = (await client.post("/api/orders", json={"location_id": await _loc(client, H), "client_id": cl,
                                                "complaint": "x", "assigned_to_id": tech}, headers=H)).json()
    await client.post(f"/api/orders/{o['id']}/lines", json={
        "line_type": "labour", "kind": "custom", "description": "job", "qty": 1, "unit_price": 500,
        "technician_id": tech}, headers=H)
    r = await client.post(f"/api/cash/orders/{o['id']}/pay", json={"method": "cash", "account_id": till,
                                                                  "amount": 500}, headers=H)
    assert r.status_code in (200, 201), r.text
    r = await client.patch(f"/api/orders/{o['id']}/status", json={"status": key}, headers=H)
    assert r.status_code == 200, r.text
    return o["id"], cl, tech


async def _commission(db, org, emp):
    from backend.models import EmployeeBalanceLedger as L
    db.expire_all()
    return float((await db.execute(select(func.coalesce(func.sum(L.amount), 0)).where(
        L.org_id == org, L.employee_id == emp, L.entry_type.in_(("commission", "clawback"))))).scalar())


async def _client_balance(db, cid):
    from backend.models import Client
    db.expire_all()
    return float((await db.execute(select(Client.balance).where(Client.id == cid))).scalar_one() or 0)


@pytest.mark.asyncio
async def test_refund_void_undoes_cash_payment_ledger_and_clawback(client, db):
    from backend.models import EmployeeBalanceLedger as L
    from backend.models import Payment, Transaction
    H, org = await _register(client)
    till = await _till(client, H)
    oid, cl, tech = await _paid_closed_order(client, H, db, org, till)
    assert await _commission(db, org, tech) == 50
    client_before = await _client_balance(db, cl)

    r = await client.post("/api/returns/refunds", json={"account_id": till, "amount": 100, "order_id": oid}, headers=H)
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    assert await _commission(db, org, tech) == 40
    assert await _account_balance(client, H, till) == pytest.approx(400)
    assert await _client_balance(db, cl) == pytest.approx(client_before + 100)

    T = await _teammate(client, H, "can_delete_refunds")
    assert (await client.delete(f"/api/returns/refunds/{rid}", headers=T)).status_code == 403

    r = await client.delete(f"/api/returns/refunds/{rid}?reason=mistake", headers=H)
    assert r.status_code == 200, r.text
    assert await _account_balance(client, H, till) == pytest.approx(500)
    assert await _client_balance(db, cl) == pytest.approx(client_before)
    assert await _commission(db, org, tech) == 50
    db.expire_all()
    assert not (await db.execute(select(Payment).where(Payment.order_id == oid, Payment.kind == "refund"))).scalars().all()
    leg = (await db.execute(select(Transaction).where(Transaction.ref_type == "refund",
                                                      Transaction.ref_id == rid))).scalar_one()
    assert leg.status == "deleted"
    # the claw-back stays; a positive entry in the same accrual group reverses it
    claw, back = (await db.execute(select(L).where(L.employee_id == tech, L.entry_type == "clawback")
                                   .order_by(L.amount))).scalars().all()
    assert float(claw.amount) == -10 and float(back.amount) == 10 and claw.accrual_key == back.accrual_key

    listed = next(x for x in (await client.get("/api/returns/refunds", headers=H)).json()["items"] if x["id"] == rid)
    assert listed["voided_at"] and listed["void_reason"] == "mistake"
    assert (await client.delete(f"/api/returns/refunds/{rid}", headers=H)).status_code == 409

    # the order counts as fully paid again, and the deleted refund no longer counts as refunded:
    # a new 100 refund takes its share of the full 500 (10), not of the 400 left (12.50)
    r = await client.post("/api/returns/refunds", json={"account_id": till, "amount": 500, "order_id": oid}, headers=H)
    assert r.status_code == 201, r.text
    assert await _commission(db, org, tech) == 0


@pytest.mark.asyncio
async def test_refund_void_share_ignores_deleted_refunds(client, db):
    H, org = await _register(client)
    till = await _till(client, H)
    oid, _cl, tech = await _paid_closed_order(client, H, db, org, till)
    rid = (await client.post("/api/returns/refunds", json={"account_id": till, "amount": 100, "order_id": oid},
                             headers=H)).json()["id"]
    assert (await client.delete(f"/api/returns/refunds/{rid}", headers=H)).status_code == 200
    await client.post("/api/returns/refunds", json={"account_id": till, "amount": 100, "order_id": oid}, headers=H)
    assert await _commission(db, org, tech) == 40


@pytest.mark.asyncio
async def test_archive_refund_is_undone_by_restoring_the_order(client, db):
    from backend.models import Refund
    H, org = await _register(client)
    till = await _till(client, H)
    oid, *_ = await _paid_closed_order(client, H, db, org, till)
    r = await client.post(f"/api/orders/{oid}/archive", json={"reason": "gone", "refund": True}, headers=H)
    assert r.status_code == 200, r.text
    rid = (await db.execute(select(Refund.id).where(Refund.order_id == oid))).scalar_one()
    assert (await client.delete(f"/api/returns/refunds/{rid}", headers=H)).status_code == 409
