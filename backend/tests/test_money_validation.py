"""Money: amounts, quantities and discounts are validated on every entry path (security phase 3).

Regressions for audit findings B4, B5, B15:
- fixed-amount discounts skipped discount approval; revenue <= 0 or a client-supplied low cost
  skipped the margin floor;
- order and POS lines accepted negative qty/price and >100% discounts (phantom stock, zero or
  negative totals);
- supplier returns / write-offs / postings accepted negative qty (adding stock) and negative costs.
"""
import uuid

import pytest
from sqlalchemy import select

from backend.models import Employee, Organisation, Part, Role
from backend.services.auth import hash_password


async def _order(client, h):
    cid = (await client.post("/api/clients", json={"name": "C"}, headers=h)).json()["id"]
    return (await client.post("/api/orders", json={"client_id": cid}, headers=h)).json()["id"]


async def _line(client, h, oid, **kw):
    body = {"line_type": "labour", "description": "Fix", "qty": 1, "unit_price": 100, **kw}
    return await client.post(f"/api/orders/{oid}/lines", headers=h, json=body)


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [{"qty": -3}, {"qty": 0}, {"unit_price": -500}, {"discount": 150},
                                 {"discount": -10}, {"discount_type": "bogus"}, {"cost_price": -1}])
async def test_order_lines_reject_invalid_amounts(client, tenant, bad):
    oid = await _order(client, tenant)
    assert (await _line(client, tenant, oid, **bad)).status_code == 422


@pytest.mark.asyncio
async def test_fixed_discount_needs_approval_like_percent(client, tenant, db):
    org = (await db.execute(select(Organisation))).scalars().first()
    org.discount_approval_pct = 10
    owner = (await db.execute(select(Employee).where(Employee.org_id == org.id))).scalars().first()
    role = Role(org_id=org.id, name="NoApprove", permissions={"can_approve_discount": False})
    db.add(role)
    await db.flush()
    email = f"na-{uuid.uuid4().hex[:6]}@val.test"
    db.add(Employee(org_id=org.id, location_id=owner.location_id, role_id=role.id, name="N", email=email,
                    password_hash=hash_password("pw-na-123456"), active=True))
    await db.commit()
    h = {"Authorization": "Bearer " + (await client.post("/api/auth/token", data={
        "username": email, "password": "pw-na-123456"})).json()["access_token"]}
    oid = await _order(client, tenant)
    assert (await _line(client, h, oid, discount=100, discount_type="fixed")).status_code == 403   # = 100%
    assert (await _line(client, h, oid, discount=5, discount_type="fixed")).status_code in (200, 201)  # 5%


@pytest.mark.asyncio
async def test_margin_floor_blocks_free_lines_and_fake_costs(client, tenant, db):
    org = (await db.execute(select(Organisation))).scalars().first()
    org.margin_guard, org.min_margin_pct = "block", 20
    part = Part(org_id=org.id, name="Screen", cost_price=50, avg_cost=50)
    db.add(part)
    await db.commit()
    oid = await _order(client, tenant)
    base = {"line_type": "part", "kind": "product", "ref_id": part.id, "description": "Screen"}
    # 100% discount -> zero revenue on a 50-cost part: was "no margin to measure" and passed
    assert (await _line(client, tenant, oid, unit_price=100, discount=100, **base)).status_code == 409
    # claiming the part cost 0.01 no longer fakes a healthy margin on a 55 sale (real margin ~9%)
    assert (await _line(client, tenant, oid, unit_price=55, cost_price=0.01, **base)).status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [{"qty": -3}, {"unit_price": -500}, {"discount": 150}])
async def test_pos_rejects_negative_or_overdiscounted_lines(client, tenant, bad):
    line = {"kind": "service", "description": "x", "qty": 1, "unit_price": 10, **bad}
    r = await client.post("/api/sales", headers=tenant, json={"lines": [line]})
    assert r.status_code == 422, r.text


def test_stock_out_doc_lines_reject_negative_qty_and_cost():
    from backend.routers.stock_docs import DocLine, PostLine
    for bad in ({"qty": -1}, {"qty": 0}, {"unit_cost": -5}):
        with pytest.raises(ValueError):
            DocLine(part_id="p", **bad)
    with pytest.raises(ValueError):
        PostLine(part_id="p", qty=-2)
