"""Wave 4 group C2: gift-card money and redemption lines.

FIN-09  A gift card spent on a job is a Payment(method='gift_card'), as at the counter, not a
        discount line. Its money goes back only to the card, never more than the job took from it,
        and a cash refund can't pay it out.
FIN-03  Redemption lines are known by order_lines.line_role, not by their description; when a line
        the customer was paying for is removed, redemptions the job no longer needs go back.
#226    Deleting a job gives back a redemption even when a sent invoice still bills its line, and
        restoring the job spends it again (or refuses, when it can't be spent again).
"""
from sqlalchemy import select, update

from backend.models import (
    AuditLog,
    GiftCard,
    Invoice,
    InvoiceLine,
    InvoiceOrder,
    LoyaltyLedger,
    Order,
    OrderLine,
    Organisation,
    Payment,
)
from backend.tests.test_p1_money_audit import _acct, _client, _job, _me, _order


async def _card(client, h, cid, value):
    r = await client.post("/api/giftcards", json={"value": value, "client_id": cid}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["code"]


async def _card_balance(client, h, code):
    return (await client.get(f"/api/giftcards/{code}", headers=h)).json()["balance"]


async def _pay(client, h, oid, till, amount):
    r = await client.post(f"/api/cash/orders/{oid}/pay", json={"method": "cash", "account_id": till,
                                                                "amount": amount}, headers=h)
    assert r.status_code == 200, r.text


async def _points(client, h, cid):
    return (await client.get(f"/api/clients/{cid}/loyalty", headers=h)).json()["points"]


async def _loyalty_on(db, org_id, points=0, cid=None):
    await db.execute(update(Organisation).where(Organisation.id == org_id).values(loyalty_point_value=1.0))
    if cid:
        db.add(LoyaltyLedger(org_id=org_id, client_id=cid, points=points, ref_type="manual", note="seed"))
    await db.commit()


# ── FIN-09 ───────────────────────────────────────────────────────────────────────────────────────

async def test_job_gift_card_redemption_is_a_gift_card_payment(client, tenant, db):
    cid = await _client(client, tenant)
    code = await _card(client, tenant, cid, 60)
    oid = await _job(client, tenant, cid, price=100)
    r = await client.post(f"/api/orders/{oid}/redeem-giftcard", json={"code": code}, headers=tenant)
    assert r.status_code == 200, r.text
    assert r.json()["redeemed"] == 60
    o = await _order(client, tenant, oid)
    assert float(o["total"]) == 100, "the job keeps its price: the card pays, it doesn't discount"
    assert [ln["description"] for ln in o["lines"]] == ["Repair"]
    db.expire_all()
    pays = (await db.execute(select(Payment).where(Payment.order_id == oid))).scalars().all()
    assert [(p.method, p.kind, float(p.amount), p.account_id) for p in pays] == [("gift_card", "payment", 60.0, None)]
    assert await _card_balance(client, tenant, code) == 0
    from backend.services.order_money import balance_due
    order = (await db.execute(select(Order).where(Order.id == oid))).scalar_one()
    assert await balance_due(db, order) == 40
    # no new money came in: the card's cash was counted when the card was sold
    me = await _me(client, tenant)
    from backend.services.revenue import cash_collected
    assert (await cash_collected(db, me["org_id"]))["in"] == 0


async def test_gift_card_money_goes_back_to_the_card_never_as_cash(client, tenant, db):
    till = await _acct(client, tenant, "Till")
    cid = await _client(client, tenant)
    code = await _card(client, tenant, cid, 60)
    oid = await _job(client, tenant, cid, price=100)
    assert (await client.post(f"/api/orders/{oid}/redeem-giftcard", json={"code": code},
                              headers=tenant)).status_code == 200
    await _pay(client, tenant, oid, till, 40)
    # a cash refund of the gift-card part is refused: only the 40 in cash can go back as cash
    r = await client.post("/api/returns/refunds", json={"account_id": till, "amount": 100, "order_id": oid,
                                                        "reason": "goodwill"}, headers=tenant)
    assert r.status_code == 409, r.text
    assert "gift card" in r.json()["detail"].lower()
    # back to the card: never more than this job took from it
    r = await client.post(f"/api/orders/{oid}/giftcard-refund", json={"code": code, "amount": 80}, headers=tenant)
    assert r.status_code == 400, r.text
    r = await client.post(f"/api/orders/{oid}/giftcard-refund", json={"code": code, "amount": 25}, headers=tenant)
    assert r.status_code == 200, r.text
    assert await _card_balance(client, tenant, code) == 25
    r = await client.post(f"/api/orders/{oid}/giftcard-refund", json={"code": code}, headers=tenant)
    assert r.status_code == 200, r.text
    assert r.json()["returned"] == 35
    assert await _card_balance(client, tenant, code) == 60
    r = await client.post(f"/api/orders/{oid}/giftcard-refund", json={"code": code}, headers=tenant)
    assert r.status_code == 400, "nothing left to give back"
    assert await _card_balance(client, tenant, code) == 60


async def test_deleting_a_job_paid_by_gift_card_gives_the_card_back_and_only_the_cash_out(client, tenant, db):
    till = await _acct(client, tenant, "Till")
    cid = await _client(client, tenant)
    code = await _card(client, tenant, cid, 70)
    oid = await _job(client, tenant, cid, price=100)
    assert (await client.post(f"/api/orders/{oid}/redeem-giftcard", json={"code": code},
                              headers=tenant)).status_code == 200
    await _pay(client, tenant, oid, till, 30)
    r = await client.post(f"/api/orders/{oid}/archive", json={"reason": "cancelled", "refund": True}, headers=tenant)
    assert r.status_code == 200, r.text
    assert r.json()["refunded"] == 30
    assert await _card_balance(client, tenant, code) == 70


# ── FIN-03 ───────────────────────────────────────────────────────────────────────────────────────

async def test_redemption_lines_carry_their_role_and_a_typed_coupon_line_is_just_a_line(client, tenant, db):
    me = await _me(client, tenant)
    cid = await _client(client, tenant)
    await _loyalty_on(db, me["org_id"], 50, cid)
    assert (await client.post("/api/coupons", json={"code": "ROLE5", "kind": "fixed", "value": 5},
                              headers=tenant)).status_code == 201
    oid = await _job(client, tenant, cid)
    assert (await client.post(f"/api/orders/{oid}/apply-coupon", json={"code": "ROLE5"},
                              headers=tenant)).status_code == 200
    assert (await client.post(f"/api/orders/{oid}/redeem-loyalty", json={"points": 10},
                              headers=tenant)).status_code == 200
    db.add(OrderLine(order_id=oid, line_type="fee", kind="custom", description="Coupon HANDTYPED", qty=1,
                     unit_price=-3, total=-3))
    await db.commit()
    db.expire_all()
    roles = {ln.description: ln.line_role for ln in (await db.execute(
        select(OrderLine).where(OrderLine.order_id == oid))).scalars().all()}
    assert roles == {"Repair": "item", "Coupon ROLE5": "coupon", "Loyalty points (10)": "loyalty",
                     "Coupon HANDTYPED": "item"}
    from backend.routers.engage import redemption_kind
    typed = (await db.execute(select(OrderLine).where(OrderLine.order_id == oid,
                                                      OrderLine.description == "Coupon HANDTYPED"))).scalar_one()
    assert redemption_kind(typed) is None, "a hand-typed line is just a line, whatever it is called"
    real = next(ln for ln in (await _order(client, tenant, oid))["lines"] if ln["description"] == "Coupon ROLE5")
    r = await client.patch(f"/api/orders/{oid}/lines/{real['id']}", json={"description": "Goodwill"}, headers=tenant)
    assert r.status_code == 409


async def _two_line_job(client, h, cid):
    r = await client.post("/api/orders", headers=h, json={"client_id": cid, "complaint": "job", "lines": [
        {"line_type": "labour", "description": "Repair", "qty": 1, "unit_price": 100},
        {"line_type": "labour", "description": "Extra", "qty": 1, "unit_price": 50}]})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def test_removing_a_paid_line_gives_back_points_the_job_no_longer_needs(client, tenant, db):
    me = await _me(client, tenant)
    till = await _acct(client, tenant, "Till")
    cid = await _client(client, tenant)
    await _loyalty_on(db, me["org_id"], 100, cid)
    oid = await _two_line_job(client, tenant, cid)
    await _pay(client, tenant, oid, till, 90)
    assert (await client.post(f"/api/orders/{oid}/redeem-loyalty", json={"points": 50},
                              headers=tenant)).status_code == 200
    assert await _points(client, tenant, cid) == 50
    extra = next(ln for ln in (await _order(client, tenant, oid))["lines"] if ln["description"] == "Extra")
    r = await client.delete(f"/api/orders/{oid}/lines/{extra['id']}", headers=tenant)
    assert r.status_code == 200, r.text
    # 100 of work, 90 paid in cash: the points cover only the 10 left, the other 40 go back
    assert await _points(client, tenant, cid) == 90
    o = await _order(client, tenant, oid)
    assert float(o["total"]) == 90
    from backend.services.order_money import overpaid
    db.expire_all()
    order = (await db.execute(select(Order).where(Order.id == oid))).scalar_one()
    assert await overpaid(db, order) == 0, "no points turned into a cash overpayment"


async def test_removing_a_paid_line_gives_back_gift_card_money_the_job_no_longer_needs(client, tenant, db):
    till = await _acct(client, tenant, "Till")
    cid = await _client(client, tenant)
    code = await _card(client, tenant, cid, 50)
    oid = await _two_line_job(client, tenant, cid)
    await _pay(client, tenant, oid, till, 90)
    assert (await client.post(f"/api/orders/{oid}/redeem-giftcard", json={"code": code},
                              headers=tenant)).status_code == 200
    assert await _card_balance(client, tenant, code) == 0
    extra = next(ln for ln in (await _order(client, tenant, oid))["lines"] if ln["description"] == "Extra")
    r = await client.delete(f"/api/orders/{oid}/lines/{extra['id']}", headers=tenant)
    assert r.status_code == 200, r.text
    # 100 of work, 90 paid in cash: the card pays the 10 left, its other 40 go back to it
    assert await _card_balance(client, tenant, code) == 40
    from backend.services.order_money import overpaid
    db.expire_all()
    order = (await db.execute(select(Order).where(Order.id == oid))).scalar_one()
    assert await overpaid(db, order) == 0


# ── #226: restore after a delete that kept a billed redemption line ──────────────────────────────

async def _job_with_points_on_a_shared_sent_invoice(client, h, db, me, till, cid):
    """A job (100) with 20 points redeemed and 30 paid in cash, on a sent invoice that also bills
    another job: deleting the job keeps the invoice (its money is on it), so the points line stays."""
    oid = await _job(client, h, cid, price=100)
    assert (await client.post(f"/api/orders/{oid}/redeem-loyalty", json={"points": 20},
                              headers=h)).status_code == 200
    await _pay(client, h, oid, till, 30)
    other = await _job(client, h, cid, price=10)
    db.expire_all()
    inv = (await db.execute(select(Invoice).join(InvoiceOrder, InvoiceOrder.invoice_id == Invoice.id, isouter=True)
                            .where((Invoice.order_id == oid) | (InvoiceOrder.order_id == oid)))).scalars().first()
    assert inv is not None
    db.add(InvoiceOrder(invoice_id=inv.id, order_id=other, sort_order=1))
    for i, ln in enumerate((await db.execute(select(OrderLine).where(OrderLine.order_id == oid))).scalars().all()):
        if not (await db.execute(select(InvoiceLine.id).where(InvoiceLine.invoice_id == inv.id,
                                                              InvoiceLine.order_line_id == ln.id))).first():
            db.add(InvoiceLine(invoice_id=inv.id, order_line_id=ln.id, kind="custom", sort_order=i,
                               description=ln.description, qty=ln.qty, unit_price=ln.unit_price, total=ln.total))
    from datetime import datetime
    inv.status, inv.sent_at = "issued", datetime(2026, 11, 1)
    await db.commit()
    return oid


async def test_deleting_a_job_gives_back_points_on_a_kept_line_and_restore_spends_them_again(client, tenant, db):
    me = await _me(client, tenant)
    till = await _acct(client, tenant, "Till")
    cid = await _client(client, tenant)
    await _loyalty_on(db, me["org_id"], 100, cid)
    oid = await _job_with_points_on_a_shared_sent_invoice(client, tenant, db, me, till, cid)
    assert await _points(client, tenant, cid) == 80
    r = await client.post(f"/api/orders/{oid}/archive", json={"reason": "cancelled", "refund": True}, headers=tenant)
    assert r.status_code == 200, r.text
    db.expire_all()
    roles = (await db.execute(select(OrderLine.line_role).where(OrderLine.order_id == oid))).scalars().all()
    assert "loyalty" in roles, "the line stays: the sent invoice still bills it"
    assert await _points(client, tenant, cid) == 100, "a deleted job holds none of the customer's points"
    r = await client.post(f"/api/orders/{oid}/restore", headers=tenant)
    assert r.status_code == 200, r.text
    assert await _points(client, tenant, cid) == 80, "the restored job's discount is paid for again"


async def test_restore_is_refused_when_the_points_were_spent_meanwhile(client, tenant, db):
    me = await _me(client, tenant)
    till = await _acct(client, tenant, "Till")
    cid = await _client(client, tenant)
    await _loyalty_on(db, me["org_id"], 20, cid)
    oid = await _job_with_points_on_a_shared_sent_invoice(client, tenant, db, me, till, cid)
    assert (await client.post(f"/api/orders/{oid}/archive", json={"reason": "x", "refund": True},
                              headers=tenant)).status_code == 200
    assert await _points(client, tenant, cid) == 20
    db.add(LoyaltyLedger(org_id=me["org_id"], client_id=cid, points=-15, ref_type="manual", note="spent elsewhere"))
    await db.commit()
    r = await client.post(f"/api/orders/{oid}/restore", headers=tenant)
    assert r.status_code == 409, r.text
    assert "points" in r.json()["detail"].lower()
    db.expire_all()
    assert (await db.execute(select(Order.archived).where(Order.id == oid))).scalar_one() is True
    assert await _points(client, tenant, cid) == 5
    acts = (await db.execute(select(AuditLog.action).where(AuditLog.entity_id == oid))).scalars().all()
    assert "order.restore" not in acts


async def test_gift_card_give_back_finds_the_card_from_its_ledger(client, tenant, db):
    """The card a payment came from is found from the ledger, not the payment's text."""
    cid = await _client(client, tenant)
    code = await _card(client, tenant, cid, 10)
    oid = await _job(client, tenant, cid, price=100)
    assert (await client.post(f"/api/orders/{oid}/redeem-giftcard", json={"code": code},
                              headers=tenant)).status_code == 200
    db.expire_all()
    await db.execute(update(Payment).where(Payment.order_id == oid).values(reference="edited by hand"))
    await db.commit()
    r = await client.post(f"/api/orders/{oid}/giftcard-refund", json={"code": code}, headers=tenant)
    assert r.status_code == 200, r.text
    db.expire_all()
    assert float((await db.execute(select(GiftCard.balance).where(GiftCard.code == code))).scalar_one()) == 10
