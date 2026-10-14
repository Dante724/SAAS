"""A2 (gift cards / coupons / loyalty) + multi-technician payroll attribution."""
import pytest
from sqlalchemy import select


async def _loc(client, tenant):
    return (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]


async def _order(client, tenant, **extra):
    body = {"location_id": await _loc(client, tenant), "complaint": "x"}
    body.update(extra)
    r = await client.post("/api/orders", json=body, headers=tenant)
    assert r.status_code in (200, 201), r.text
    return r.json()


@pytest.mark.asyncio
async def test_multi_tech_lines_pay_each_technician(client, tenant, db):
    """Two techs on one work order, each earns commission on THEIR lines
    (Orderry: 'each employee receives commission for their labors')."""
    from backend.models import CommissionRule, Employee, EmployeeBalanceLedger
    from backend.services.auth import hash_password

    me = (await client.get("/api/auth/me", headers=tenant)).json()
    t2 = Employee(org_id=me["org_id"], name="Tech Two", email="t2@x.dev",
                  password_hash=hash_password("pw-123456789"), active=True)
    db.add(t2)
    db.add(CommissionRule(org_id=me["org_id"], employee_id=None, name="Labor 10%",
                          event="labor_performed", calc_type="percent",
                          percent_basis="line_total", amount=10.0))
    await db.commit()
    await db.refresh(t2)

    term = await client.post("/api/statuses", json={
        "name": "Done MT", "key": "done-mt", "status_group": "closed", "is_terminal": True}, headers=tenant)
    assert term.status_code == 201
    o = await _order(client, tenant)
    for tech, price in ((me["id"], 100.0), (t2.id, 50.0)):
        r = await client.post(f"/api/orders/{o['id']}/lines", json={
            "line_type": "labour", "kind": "custom", "description": "task",
            "qty": 1, "unit_price": price, "technician_id": tech}, headers=tenant)
        assert r.status_code in (200, 201), r.text
    closed = await client.patch(f"/api/orders/{o['id']}/status", json={"status": "done-mt"}, headers=tenant)
    assert closed.status_code == 200, closed.text

    rows = (await db.execute(select(EmployeeBalanceLedger).where(
        EmployeeBalanceLedger.event == "labor_performed"))).scalars().all()
    by_emp = {r.employee_id: r.amount for r in rows}
    assert by_emp.get(me["id"]) == pytest.approx(10.0)
    assert by_emp.get(t2.id) == pytest.approx(5.0)


@pytest.mark.asyncio
async def test_line_technician_patchable(client, tenant, db):
    from backend.models import Employee
    from backend.services.auth import hash_password
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    t2 = Employee(org_id=me["org_id"], name="T3", email="t3@x.dev",
                  password_hash=hash_password("pw-123456789"), active=True)
    db.add(t2)
    await db.commit()
    await db.refresh(t2)
    o = await _order(client, tenant)
    r0 = await client.post(f"/api/orders/{o['id']}/lines", json={
        "line_type": "labour", "kind": "custom", "description": "t", "qty": 1, "unit_price": 5}, headers=tenant)
    assert r0.status_code in (200, 201), r0.text
    lid = (await client.get(f"/api/orders/{o['id']}", headers=tenant)).json()["lines"][-1]["id"]
    r = await client.patch(f"/api/orders/{o['id']}/lines/{lid}", json={"technician_id": t2.id}, headers=tenant)
    assert r.status_code == 200, r.text
    detail = (await client.get(f"/api/orders/{o['id']}", headers=tenant)).json()
    assert any(ln.get("technician_id") == t2.id for ln in detail["lines"])


@pytest.mark.asyncio
async def test_order_created_commission(client, tenant, db):
    from backend.models import CommissionRule, EmployeeBalanceLedger
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    db.add(CommissionRule(org_id=me["org_id"], employee_id=None, name="Create $2",
                          event="order_created", calc_type="flat",
                          percent_basis="total", amount=2.0))
    await db.commit()
    o = await _order(client, tenant)
    rows = (await db.execute(select(EmployeeBalanceLedger).where(
        EmployeeBalanceLedger.event == "order_created",
        EmployeeBalanceLedger.ref_id == o["id"]))).scalars().all()
    assert rows and rows[0].employee_id == me["id"] and rows[0].amount == pytest.approx(2.0)


@pytest.mark.asyncio
async def test_gift_card_lifecycle(client, tenant):
    cid = (await client.post("/api/clients", json={"name": "GC Owner"}, headers=tenant)).json()["id"]
    g = await client.post("/api/giftcards", json={"value": 50, "client_id": cid}, headers=tenant)
    assert g.status_code == 201, g.text
    code = g.json()["code"]
    assert (await client.get(f"/api/giftcards/{code}", headers=tenant)).json()["balance"] == 50

    o = await _order(client, tenant)
    await client.post(f"/api/orders/{o['id']}/lines", json={
        "line_type": "part", "kind": "custom", "description": "p", "qty": 1, "unit_price": 30}, headers=tenant)
    r = await client.post(f"/api/orders/{o['id']}/redeem-giftcard", json={"code": code}, headers=tenant)
    assert r.status_code == 200 and r.json()["redeemed"] == pytest.approx(30.0)
    assert r.json()["card_balance"] == pytest.approx(20.0)
    detail = (await client.get(f"/api/orders/{o['id']}", headers=tenant)).json()
    assert detail["total"] == pytest.approx(0.0)
    over = await client.post(f"/api/orders/{o['id']}/redeem-giftcard",
                             json={"code": code, "amount": 999}, headers=tenant)
    assert over.status_code == 400


@pytest.mark.asyncio
async def test_coupon_rules(client, tenant):
    c = await client.post("/api/coupons", json={
        "code": "SAVE10", "kind": "pct", "value": 10, "min_total": 20, "max_uses": 1}, headers=tenant)
    assert c.status_code == 201, c.text
    o = await _order(client, tenant)
    await client.post(f"/api/orders/{o['id']}/lines", json={
        "line_type": "part", "kind": "custom", "description": "p", "qty": 1, "unit_price": 100}, headers=tenant)
    ok = await client.post(f"/api/orders/{o['id']}/apply-coupon", json={"code": "save10"}, headers=tenant)
    assert ok.status_code == 200 and ok.json()["discount"] == pytest.approx(10.0)
    dup = await client.post(f"/api/orders/{o['id']}/apply-coupon", json={"code": "SAVE10"}, headers=tenant)
    assert dup.status_code == 400
    o2 = await _order(client, tenant)
    await client.post(f"/api/orders/{o2['id']}/lines", json={
        "line_type": "part", "kind": "custom", "description": "p", "qty": 1, "unit_price": 100}, headers=tenant)
    exhausted = await client.post(f"/api/orders/{o2['id']}/apply-coupon", json={"code": "SAVE10"}, headers=tenant)
    assert exhausted.status_code == 400  # max_uses reached


@pytest.mark.asyncio
async def test_loyalty_earn_and_redeem(client, tenant, db):
    from backend.models import Client as ClientM
    from backend.models import Organisation
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    org = (await db.execute(select(Organisation).where(Organisation.id == me["org_id"]))).scalar_one()
    org.loyalty_earn_rate = 10.0     # 10 pts per 1.00 paid
    org.loyalty_point_value = 0.05   # 1 pt = 0.05
    cl = ClientM(org_id=me["org_id"], name="Loyal Lou")
    db.add(cl)
    await db.commit()
    await db.refresh(cl)

    o = await _order(client, tenant, client_id=cl.id)
    await client.post(f"/api/orders/{o['id']}/lines", json={
        "line_type": "part", "kind": "custom", "description": "p", "qty": 1, "unit_price": 60}, headers=tenant)
    link = (await client.post(f"/api/orders/{o['id']}/payment-link", json={}, headers=tenant)).json()
    paid = await client.post(f"/api/payments/intents/{link['intent_id']}/simulate-pay")
    assert paid.status_code == 200

    loy = (await client.get(f"/api/clients/{cl.id}/loyalty", headers=tenant)).json()
    assert loy["points"] == pytest.approx(600.0)      # 60 * 10
    assert loy["tier"] == "silver"                    # 600 lifetime >= 500

    o2 = await _order(client, tenant, client_id=cl.id)
    await client.post(f"/api/orders/{o2['id']}/lines", json={
        "line_type": "part", "kind": "custom", "description": "p", "qty": 1, "unit_price": 40}, headers=tenant)
    red = await client.post(f"/api/orders/{o2['id']}/redeem-loyalty", json={"points": 200}, headers=tenant)
    assert red.status_code == 200 and red.json()["discount"] == pytest.approx(10.0)
    too_many = await client.post(f"/api/orders/{o2['id']}/redeem-loyalty", json={"points": 99999}, headers=tenant)
    assert too_many.status_code == 400
