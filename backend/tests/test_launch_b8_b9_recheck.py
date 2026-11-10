"""Launch blockers B8/B9, second pass (independent recheck found what the first fix missed).

B8: My Work still bound one parameter per device for a tech with 33,500 open jobs ("too many SQL
variables"), checked the next status once per job, and the Go app's Today loaded every open job.
B9: ?closing_float=nan saved NaN on a closed drawer and broke the shifts list for good; an import
cell of 1e999 and a 1e307 price change were a 500; a NaN stored before the fix took the parts
list down with no way back but SQL."""
import uuid

import pytest
from sqlalchemy import insert, select, text, update

from backend.tests.test_launch_b8_scale import MAX_PARAMS, _bulk_orders, _me, _watch

N = 300


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


# ── B8 ───────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_my_work_when_tech_owns_every_open_job(client, tenant, db):
    """A solo owner is the tech on all of the shop's open jobs, each with a device."""
    from backend.models import Device, Order, OrderLine
    me = await _me(client, tenant)
    loc = await _loc(client, tenant)
    ids = await _bulk_orders(db, me["org_id"], loc, N)
    devs = [dict(id=str(uuid.uuid4()), org_id=me["org_id"], brand="Apple", model=f"iPhone {i}")
            for i in range(N)]
    await db.execute(insert(Device), devs)
    for oid, d in zip(ids, devs):
        await db.execute(update(Order).where(Order.id == oid).values(device_id=d["id"]))
    await db.execute(insert(OrderLine), [dict(id=str(uuid.uuid4()), order_id=oid, kind="service",
                                              line_type="labour", description="Fix", qty=1, unit_price=10,
                                              technician_id=me["id"]) for oid in ids])
    await db.commit()

    with _watch(db) as s:
        r = await client.get("/api/timeclock/my-work", headers=tenant)
    assert r.status_code == 200, r.text
    assert s["max_params"] <= MAX_PARAMS, f"My Work bound {s['max_params']} parameters"
    assert s["statements"] < 60, f"My Work ran {s['statements']} statements (one per job?)"
    orders = r.json()["orders"]
    assert len(orders) == N
    assert all(o["device"].startswith("Apple iPhone") for o in orders)
    assert all(len(o["lines"]) == 1 for o in orders)


@pytest.mark.asyncio
async def test_tech_dashboard_does_not_load_every_open_job(client, tenant, db):
    from backend.models import Order
    from backend.routers.tech import LIST_CAP
    me = await _me(client, tenant)
    loc = await _loc(client, tenant)
    await _bulk_orders(db, me["org_id"], loc, N, assigned_to_id=me["id"])   # all open, unscheduled

    with _watch(db, Order) as s:
        r = await client.get("/api/tech/dashboard", headers=tenant)
    assert r.status_code == 200, r.text
    body = r.json()
    overdue = len([i for i in range(N) if i % 7 == 0])
    assert body["stats"] == {"today": N, "upcoming": 0, "completed": 0, "overdue": overdue,
                             "assigned_total": N}
    assert len(body["today"]) == LIST_CAP and len(body["overdue"]) == overdue
    assert s["loaded"] <= LIST_CAP + overdue, f"Today loaded {s['loaded']} work orders"
    assert s["max_params"] <= MAX_PARAMS


# ── B9: inputs ───────────────────────────────────────────────────────────────────

async def _open_shift(client, h):
    r = await client.post("/api/finance/shifts/open",
                          json={"location_id": await _loc(client, h), "opening_float": 100}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()["id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["nan", "NaN", "inf", "-Infinity"])
async def test_cash_drawer_close_refuses_nan_in_the_query(client, tenant, value):
    sid = await _open_shift(client, tenant)
    r = await client.post(f"/api/finance/shifts/{sid}/close?closing_float={value}", headers=tenant)
    assert r.status_code == 422, r.text
    r = await client.get("/api/finance/shifts", headers=tenant)
    assert r.status_code == 200, r.text
    r = await client.post(f"/api/finance/shifts/{sid}/close?closing_float=100", headers=tenant)
    assert r.status_code == 200, r.text      # still open, so it can be closed properly


@pytest.mark.asyncio
@pytest.mark.parametrize("denoms", [{"nan": 1}, {"20": "inf"}, {"Infinity": 2}])
async def test_cash_drawer_denominations_refuse_nan(client, tenant, denoms):
    r = await client.post("/api/finance/shifts/open", json={"location_id": await _loc(client, tenant),
                                                            "denoms": denoms}, headers=tenant)
    assert r.status_code == 422, r.text
    sid = await _open_shift(client, tenant)
    r = await client.post(f"/api/finance/shifts/{sid}/close", json={"denoms": denoms}, headers=tenant)
    assert r.status_code == 422, r.text
    assert (await client.get("/api/finance/shifts", headers=tenant)).status_code == 200


def test_every_query_string_number_refuses_nan():
    """Query floats skip the BaseModel default, so each one opts out of NaN itself."""
    from fastapi.routing import APIRoute

    import main

    def routes(rs):
        for r in rs:
            if isinstance(r, APIRoute):
                yield r
            elif hasattr(r, "original_router"):
                yield from routes(r.original_router.routes)

    def params(d):
        yield from d.query_params + d.path_params + d.header_params + d.cookie_params
        for sub in d.dependencies:
            yield from params(sub)

    seen, loose = [], []
    for r in routes(main.app.routes):
        for p in params(r.dependant):
            if any(t in str(p.field_info.annotation) for t in ("float", "Decimal")):
                seen.append(f"{r.path} {p.name}")
                if not any(getattr(m, "allow_inf_nan", None) is False for m in p.field_info.metadata):
                    loose.append(f"{r.path} {p.name}")
    assert seen and not loose, loose


@pytest.mark.asyncio
async def test_import_cell_too_large_is_a_row_error(client, tenant, db):
    from backend.models import Part
    from backend.tests.test_import_centre import _parse, _run, _xlsx
    rows = [["Name", "SKU", "Sell price", "Cost price"],
            ["Huge", "H-1", "1e999", "1"], ["Huge too", "H-2", "1", "1E400"],
            ["Too big", "H-3", "1e300", "1"], ["Fine", "F-1", "12.5", "4"]]
    parsed = await _parse(client, tenant, "products", _xlsx(rows))
    for p in parsed["rows"]:       # keep the text: a spreadsheet cell can hold "1e999" as text
        p[:] = [str(x) for x in p]
    r = await _run(client, tenant, "products", parsed, commit=True)
    assert r.status_code == 200, r.text
    assert r.json()["created"] == 1 and r.json()["failed"] == 3
    names = set((await db.execute(select(Part.name).where(Part.sku.in_(["H-1", "H-2", "H-3", "F-1"])))).scalars())
    assert names == {"Fine"}


@pytest.mark.asyncio
async def test_stocktake_import_refuses_nan_counts(client, tenant):
    loc = await _loc(client, tenant)
    p = (await client.post("/api/catalog/products", json={"name": "Cable", "sku": "C-NAN"}, headers=tenant)).json()
    st = (await client.post("/api/stocktake", json={"location_id": loc}, headers=tenant)).json()["id"]
    r = await client.post(f"/api/stocktake/{st}/import", json={"rows": [
        {"code": "C-NAN", "counted_qty": "nan"}, {"code": "C-NAN", "counted_qty": "inf"}]}, headers=tenant)
    assert r.status_code == 200, r.text
    assert [e["row"] for e in r.json()["errors"]] == [1, 2], r.json()
    lines = (await client.get(f"/api/stocktake/{st}/lines", headers=tenant)).json()["items"]
    assert all(i["counted_qty"] in (None, 0) for i in lines if i["part_id"] == p["id"])


@pytest.mark.asyncio
async def test_huge_finite_price_change_is_a_422(client, tenant):
    p = (await client.post("/api/catalog/products", json={"name": "Screen", "cost_price": 10,
                                                          "sell_price": 20}, headers=tenant)).json()
    r = await client.post("/api/catalog/products/bulk", json={"ids": [p["id"]], "action": "price",
                                                              "price_pct": 1e307}, headers=tenant)
    assert r.status_code == 422, r.text
    r = await client.post("/api/inventory/parts/apply-markup", json={"markup_pct": 1e308,
                                                                     "basis": "cost_price"}, headers=tenant)
    assert r.status_code == 422, r.text
    got = (await client.get(f"/api/catalog/products/{p['id']}", headers=tenant)).json()
    assert got["product"]["sell_price"] == 20


# ── B9: values stored before the fix ─────────────────────────────────────────────

def _bad(db) -> str:
    """A non-finite value as the database stores it: Postgres keeps NaN; SQLite turns NaN into
    NULL but keeps Infinity."""
    return "'NaN'" if db.bind.dialect.name == "postgresql" else "9e999"


@pytest.mark.asyncio
async def test_stored_infinity_does_not_take_the_lists_down(client, tenant, db):
    pid = (await client.post("/api/inventory/parts", json={"name": "Old bad part", "cost_price": 5},
                             headers=tenant)).json()["id"]
    await db.execute(text(f"UPDATE parts SET cost_price = {_bad(db)} WHERE id = :id"), {"id": pid})
    await db.commit()
    for url in ("/api/inventory/parts", "/api/catalog/products"):
        r = await client.get(url, headers=tenant)
        assert r.status_code == 200, (url, r.text)
        assert "Infinity" not in r.text and "NaN" not in r.text
    item = next(i for i in (await client.get("/api/inventory/parts", headers=tenant)).json()["items"]
                if i["id"] == pid)
    assert item["cost_price"] is None          # shown blank, so the user can enter the real cost
    r = await client.put(f"/api/inventory/parts/{pid}", json={"name": "Old bad part", "cost_price": 7},
                         headers=tenant)
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_stored_infinity_in_a_closed_drawer_is_readable(client, tenant, db):
    sid = await _open_shift(client, tenant)
    await client.post(f"/api/finance/shifts/{sid}/close?closing_float=10", headers=tenant)
    await db.execute(text(f"UPDATE cash_shifts SET closing_float = {_bad(db)} WHERE id = :id"), {"id": sid})
    await db.commit()
    r = await client.get("/api/finance/shifts", headers=tenant)
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_migration_clears_stored_non_finite_values(client, tenant, db):
    from backend.core.finite_numbers import clear_non_finite
    from backend.models import Part
    pid = (await client.post("/api/inventory/parts", json={"name": "Bad", "cost_price": 5, "sell_price": 9},
                             headers=tenant)).json()["id"]
    await db.execute(text(f"UPDATE parts SET cost_price = {_bad(db)}, sell_price = {_bad(db)} WHERE id = :id"),
                     {"id": pid})
    await db.commit()
    conn = await db.connection()
    assert await conn.run_sync(clear_non_finite) >= 1
    await db.commit()
    row = (await db.execute(select(Part.cost_price, Part.sell_price).where(Part.id == pid))).one()
    assert all(v in (None, 0) for v in row), row
    conn = await db.connection()
    assert await conn.run_sync(clear_non_finite) == 0      # nothing left to clear


@pytest.mark.asyncio
async def test_a_non_finite_number_is_never_written(client, tenant, db):
    """Last line of defence: whatever path a NaN takes, it is refused at the database write."""
    from backend.core.finite_numbers import NonFiniteValue
    from backend.models import Part
    me = await _me(client, tenant)
    for bad in (float("nan"), float("inf")):
        db.add(Part(org_id=me["org_id"], name="Sneaky", cost_price=bad))
        with pytest.raises(NonFiniteValue):
            await db.flush()
        await db.rollback()
    p = Part(org_id=me["org_id"], name="Fine", cost_price=3)
    db.add(p)
    await db.commit()
    p.sell_price = float("-inf")
    with pytest.raises(NonFiniteValue):
        await db.commit()
    await db.rollback()
