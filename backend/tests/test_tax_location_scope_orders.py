"""Location-scoped taxes reach work-order lines, and a mixed included/imposed stack is refused
rather than silently summed into one rate with the first tax's mode."""
import pytest
from sqlalchemy import select

from backend.models import OrderLine


async def _locs(client, h):
    main = (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]
    other = (await client.post("/api/staff/locations", json={"name": "Branch"}, headers=h)).json()["id"]
    return main, other


async def _tax(client, h, name, rate, mode="imposed", **extra):
    r = await client.post("/api/catalog/taxes", json={"name": name, "rate": rate, "mode": mode, **extra},
                          headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _stack(client, h, *tax_ids):
    r = await client.put("/api/settings/tax-rules",
                         json={"doc_family": "tickets_sales", "tax_ids": list(tax_ids)}, headers=h)
    assert r.status_code == 200, r.text


async def _order(client, h, loc, lines=None):
    cid = (await client.post("/api/clients", json={"name": "C"}, headers=h)).json()["id"]
    r = await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "lines": lines or []},
                          headers=h)
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


_LINE = {"line_type": "labour", "kind": "service", "description": "Diag", "qty": 1, "unit_price": 100}


@pytest.mark.asyncio
async def test_location_scoped_tax_applied_on_add_line(client, tenant, db):
    main, other = await _locs(client, tenant)
    gst = await _tax(client, tenant, "GST", 5.0)
    pst = await _tax(client, tenant, "PST", 7.0, location_scope="selected", location_ids=[main])
    await _stack(client, tenant, gst, pst)

    here = await _order(client, tenant, main)
    there = await _order(client, tenant, other)
    for oid in (here, there):
        r = await client.post(f"/api/orders/{oid}/lines", json=_LINE, headers=tenant)
        assert r.status_code == 201, r.text

    rate = {oid: (await db.execute(select(OrderLine.tax_rate).where(OrderLine.order_id == oid))).scalar_one()
            for oid in (here, there)}
    assert rate[here] == pytest.approx(12.0)   # GST + PST (PST is scoped to this location)
    assert rate[there] == pytest.approx(5.0)   # GST only


@pytest.mark.asyncio
async def test_location_scoped_tax_applied_on_order_create(client, tenant, db):
    main, _ = await _locs(client, tenant)
    pst = await _tax(client, tenant, "PST", 7.0, location_scope="selected", location_ids=[main])
    await _stack(client, tenant, pst)
    oid = await _order(client, tenant, main, lines=[_LINE])
    line = (await db.execute(select(OrderLine).where(OrderLine.order_id == oid))).scalar_one()
    assert line.tax_rate == pytest.approx(7.0)
    assert line.total == pytest.approx(107.0)


@pytest.mark.asyncio
async def test_mixed_included_and_imposed_stack_is_refused(client, tenant):
    main, _ = await _locs(client, tenant)
    imposed = await _tax(client, tenant, "Sales tax", 5.0, mode="imposed")
    included = await _tax(client, tenant, "VAT", 20.0, mode="included")
    await _stack(client, tenant, imposed, included)
    oid = await _order(client, tenant, main)
    r = await client.post(f"/api/orders/{oid}/lines", json=_LINE, headers=tenant)
    assert r.status_code == 422
    assert "mixes included and imposed" in r.json()["detail"]

    # An explicit tax on the line bypasses the default stack, so the job isn't blocked.
    ok = await client.post(f"/api/orders/{oid}/lines", json={**_LINE, "tax_id": imposed, "tax_rate": 5.0},
                           headers=tenant)
    assert ok.status_code == 201, ok.text
