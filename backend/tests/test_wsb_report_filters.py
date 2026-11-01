"""Workstream B — report filters (gap #6): product turnover with / without movement, closed jobs
without zero amounts. Unknown filter values are ignored rather than erroring."""
import pytest
from sqlalchemy import select

pytestmark = pytest.mark.asyncio


async def _parts(db, tenant_client, H):
    from backend.models import Employee, Part, StockMovement
    me = (await tenant_client.get("/api/auth/me", headers=H)).json()
    org = (await db.execute(select(Employee.org_id).where(Employee.id == me["id"]))).scalar()
    used = Part(org_id=org, name="Battery", sku="BAT-1", active=True)
    idle = Part(org_id=org, name="Charger", sku="CHG-1", active=True)
    gone = Part(org_id=org, name="Old part", sku="OLD", active=False)
    db.add_all([used, idle, gone])
    await db.flush()
    loc = (await tenant_client.get("/api/staff/locations", headers=H)).json()
    loc = (loc if isinstance(loc, list) else loc["items"])[0]["id"]
    db.add(StockMovement(part_id=used.id, location_id=loc, movement_type="use", qty=-2, unit_cost=5,
                         ref_type="sale", ref_id="x"))
    await db.commit()


async def _rows(client, H, key, **params):
    r = await client.get(f"/api/reports/run/{key}", params=params, headers=H)
    assert r.status_code == 200, r.text
    return r.json()["rows"]


async def test_turnover_with_and_without_movement(client, tenant, db):
    await _parts(db, client, tenant)
    assert [r[0] for r in await _rows(client, tenant, "product_turnover")] == ["Battery"]
    assert [r[0] for r in await _rows(client, tenant, "product_turnover", movement="with")] == ["Battery"]
    without = await _rows(client, tenant, "product_turnover", movement="without")
    assert [r[0] for r in without] == ["Charger"]          # active only
    assert without[0][2] == 0
    both = [r[0] for r in await _rows(client, tenant, "product_turnover", movement="all")]
    assert sorted(both) == ["Battery", "Charger"]
    # nonsense falls back to the default
    assert [r[0] for r in await _rows(client, tenant, "product_turnover", movement="bogus")] == ["Battery"]


async def test_closed_jobs_can_hide_zero_amounts(client, tenant):
    zero = (await client.post("/api/orders", json={"complaint": "Look at it"}, headers=tenant)).json()["id"]
    paid = (await client.post("/api/orders", json={"complaint": "Fix it"}, headers=tenant)).json()["id"]
    await client.post(f"/api/orders/{paid}/lines", json={"line_type": "labour", "kind": "service",
                                                         "description": "Repair", "qty": 1, "unit_price": 50},
                      headers=tenant)
    for oid in (zero, paid):
        assert (await client.patch(f"/api/orders/{oid}/status", json={"status": "done"}, headers=tenant)).status_code == 200
    assert len(await _rows(client, tenant, "wo_closed")) == 2
    rows = await _rows(client, tenant, "wo_closed", hide_zero="true")
    assert len(rows) == 1 and rows[0][-1] == 50


async def test_filters_also_apply_to_exports(client, tenant, db):
    await _parts(db, client, tenant)
    r = await client.get("/api/reports/export-csv/product_turnover", params={"movement": "without"}, headers=tenant)
    assert r.status_code == 200
    assert "Charger" in r.text and "Battery" not in r.text


async def test_odd_filter_values_are_ignored_not_refused(client, tenant):
    r = await client.get("/api/reports/run/wo_closed", params={"hide_zero": "maybe"}, headers=tenant)
    assert r.status_code == 200, r.text
