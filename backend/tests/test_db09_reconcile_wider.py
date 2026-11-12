"""Audit DB-09: the nightly integrity sweep also re-derives gift-card balances, supplier balances
and the order, sale and purchase-order totals from what they summarise.

Money flows through the API first (nothing must be reported), then one stored total at a time is
corrupted behind the API's back and the sweep must name exactly that row. The sweep reports; it
never repairs (the stored value is left as it was).
"""
import pytest
from sqlalchemy import select, update

from backend.models import GiftCard, Order, PurchaseOrder, Sale, Supplier
from backend.services import reconcile

NEW_CHECKS = ("giftcard", "supplier", "order", "sale", "po")


async def _shop(client, h, db):
    from backend.models import Client as ClientM
    org = (await client.get("/api/auth/me", headers=h)).json()["org_id"]
    loc = (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]
    cid = (await client.post("/api/clients", json={"name": "Recon"}, headers=h)).json()["id"]
    # a gift card, part spent at the counter
    g = (await client.post("/api/giftcards", json={"value": 40, "client_id": cid}, headers=h)).json()
    r = await client.post("/api/sales", headers=h, json={
        "lines": [{"kind": "service", "ref_id": None, "description": "Svc", "qty": 2, "unit_price": 15}],
        "discount": 5, "gift_card_code": g["code"]})
    assert r.status_code == 201, r.text
    sale = r.json()["id"]
    gid = (await db.execute(select(GiftCard.id).where(GiftCard.code == g["code"]))).scalar_one()
    # a job with lines
    r = await client.post("/api/orders", headers=h, json={
        "client_id": cid, "location_id": loc,
        "lines": [{"line_type": "labour", "description": "Fix", "qty": 1, "unit_price": 100},
                  {"line_type": "labour", "description": "Clean", "qty": 2, "unit_price": 12.5}]})
    assert r.status_code == 201, r.text
    oid = r.json()["id"]
    # a supplier mirrored by a contact, bought from on credit
    c = ClientM(org_id=org, name="Vendor", is_supplier=True)
    db.add(c)
    await db.flush()
    sup = Supplier(org_id=org, client_id=c.id, name="Vendor", balance=0)
    db.add(sup)
    await db.commit()
    sid = sup.id
    pid = (await client.post("/api/inventory/parts", json={"name": "Pad", "sku": "RC-PAD"}, headers=h)).json()["id"]
    r = await client.post("/api/inventory/po", headers=h, json={
        "supplier_id": sid, "location_id": loc, "on_credit": True,
        "lines": [{"part_id": pid, "qty_ordered": 3, "unit_cost": 7.5}]})
    assert r.status_code in (200, 201), r.text
    po = r.json()["po_id"]
    r = await client.patch(f"/api/inventory/po/{po}", headers=h, json={
        "charges": [{"label": "Shipping", "amount": 4}],
        "lines": [{"part_id": pid, "qty_ordered": 3, "unit_cost": 7.5}]})
    assert r.status_code == 200, r.text
    r = await client.post(f"/api/inventory/po/{po}/receive", headers=h, json={})
    assert r.status_code == 200, r.text
    db.expire_all()
    assert float((await db.execute(select(Supplier.balance).where(Supplier.id == sid))).scalar_one()) != 0
    return {"org": org, "giftcard": gid, "sale": sale, "order": oid, "supplier": sid, "po": po}


def test_the_new_checks_are_registered():
    for name in NEW_CHECKS:
        assert name in reconcile.CHECKS


@pytest.mark.asyncio
async def test_real_flows_reconcile_on_every_check(client, tenant, db):
    s = await _shop(client, tenant, db)
    assert await reconcile.find_mismatches(db, org_id=s["org"]) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("check", NEW_CHECKS)
async def test_each_new_kind_of_drift_is_reported_not_fixed(client, tenant, db, check):
    s = await _shop(client, tenant, db)
    model, col = {"giftcard": (GiftCard, "balance"), "supplier": (Supplier, "balance"),
                  "order": (Order, "total"), "sale": (Sale, "total"),
                  "po": (PurchaseOrder, "total")}[check]
    await db.execute(update(model).where(model.id == s[check])
                     .values({col: getattr(model, col) + 3}))
    await db.commit()
    before = float((await db.execute(select(getattr(model, col)).where(model.id == s[check]))).scalar_one())
    found = await reconcile.find_mismatches(db, org_id=s["org"])
    assert [(m.check, m.entity_id) for m in found] == [(check, s[check])], found
    assert found[0].diff == pytest.approx(3)
    db.expire_all()
    after = float((await db.execute(select(getattr(model, col)).where(model.id == s[check]))).scalar_one())
    assert after == before          # reported, never auto-fixed


@pytest.mark.asyncio
async def test_a_voided_sale_is_not_reported(client, tenant, db):
    s = await _shop(client, tenant, db)
    r = await client.delete(f"/api/sales/{s['sale']}", headers=tenant)
    assert r.status_code == 200, r.text
    db.expire_all()
    assert await reconcile.find_mismatches(db, org_id=s["org"]) == []


@pytest.mark.asyncio
async def test_shop_rounding_is_not_drift(client, tenant, db):
    from backend.models import Organisation
    s = await _shop(client, tenant, db)
    await db.execute(update(Organisation).where(Organisation.id == s["org"]).values(rounding_enabled=True))
    await db.execute(update(Order).where(Order.id == s["order"]).values(total=Order.total + 0.02))
    await db.commit()
    assert await reconcile.find_mismatches(db, org_id=s["org"]) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("to_cost", (True, False))
async def test_recost_keeps_the_po_total_in_step_with_its_charges(client, tenant, db, to_cost):
    """The late-freight recost wrote the PO's charges but left its total at the line subtotal;
    the po check above found it on every recost."""
    s = await _shop(client, tenant, db)
    r = await client.post(f"/api/inventory/po/{s['po']}/recost", headers=tenant, json={
        "charges": [{"label": "Freight", "amount": 9}], "charges_to_cost": to_cost})
    assert r.status_code == 200, r.text
    db.expire_all()
    total = float((await db.execute(select(PurchaseOrder.total).where(PurchaseOrder.id == s["po"]))).scalar_one())
    assert total == pytest.approx(3 * 7.5 + 9)
    assert await reconcile.find_mismatches(db, org_id=s["org"], checks=("po",)) == []
