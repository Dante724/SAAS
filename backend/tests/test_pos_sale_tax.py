"""POS sales resolve tax like work orders: the default stack for the sale's location, exceptions
honoured, per-line tax stored with a breakdown, and the sale's tax_total recorded."""
import json

import pytest
from sqlalchemy import select

from backend.models import Sale, SaleLine


async def _tax(client, h, name, rate, mode="imposed", **extra):
    r = await client.post("/api/catalog/taxes", json={"name": name, "rate": rate, "mode": mode, **extra},
                          headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _stack(client, h, *tax_ids):
    r = await client.put("/api/settings/tax-rules",
                         json={"doc_family": "tickets_sales", "tax_ids": list(tax_ids)}, headers=h)
    assert r.status_code == 200, r.text


async def _main_loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


async def _sell(client, h, lines, **extra):
    r = await client.post("/api/sales", json={"lines": lines, **extra}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


_SVC = {"kind": "service", "description": "Screen fit", "qty": 1, "unit_price": 100}


@pytest.mark.asyncio
async def test_sale_applies_stack_including_location_scoped_tax(client, tenant, db):
    main = await _main_loc(client, tenant)
    gst = await _tax(client, tenant, "GST", 5.0)
    pst = await _tax(client, tenant, "PST", 7.0, location_scope="selected", location_ids=[main])
    await _stack(client, tenant, gst, pst)

    s = await _sell(client, tenant, [_SVC])
    assert s["subtotal"] == pytest.approx(100.0)
    assert s["tax_total"] == pytest.approx(12.0)
    assert s["total"] == pytest.approx(112.0)
    assert s["profit"] == pytest.approx(100.0)       # tax is not revenue

    sale = (await db.execute(select(Sale).where(Sale.id == s["id"]))).scalar_one()
    line = (await db.execute(select(SaleLine).where(SaleLine.sale_id == s["id"]))).scalar_one()
    assert sale.tax_total == pytest.approx(12.0) and sale.total == pytest.approx(112.0)
    assert line.tax_amount == pytest.approx(12.0)
    assert {t["name"]: t["amount"] for t in json.loads(line.tax_detail)} == {"GST": 5.0, "PST": 7.0}


@pytest.mark.asyncio
async def test_sale_skips_tax_scoped_to_another_location(client, tenant):
    other = (await client.post("/api/staff/locations", json={"name": "Branch"}, headers=tenant)).json()["id"]
    pst = await _tax(client, tenant, "PST", 7.0, location_scope="selected", location_ids=[other])
    await _stack(client, tenant, pst)
    s = await _sell(client, tenant, [_SVC])
    assert s["tax_total"] == 0 and s["total"] == pytest.approx(100.0)


@pytest.mark.asyncio
async def test_sale_honours_tax_exception(client, tenant, db):
    gst = await _tax(client, tenant, "GST", 5.0)
    await _stack(client, tenant, gst)
    svc = (await client.post("/api/catalog/services", json={"name": "Exempt work", "price": 50},
                             headers=tenant)).json()["id"]
    r = await client.post("/api/settings/tax-exceptions", json={
        "doc_family": "tickets_sales", "target_type": "service", "target_id": svc, "exempt": True},
        headers=tenant)
    assert r.status_code == 201, r.text

    s = await _sell(client, tenant, [_SVC, {"kind": "service", "ref_id": svc, "description": "Exempt",
                                           "qty": 1, "unit_price": 50}])
    assert s["tax_total"] == pytest.approx(5.0)       # only the non-exempt 100
    assert s["total"] == pytest.approx(155.0)


@pytest.mark.asyncio
async def test_sale_included_tax_does_not_change_total(client, tenant):
    vat = await _tax(client, tenant, "HST incl", 13.0, mode="included")
    await _stack(client, tenant, vat)
    s = await _sell(client, tenant, [{**_SVC, "unit_price": 113}])
    assert s["total"] == pytest.approx(113.0)
    assert s["tax_total"] == pytest.approx(13.0)


@pytest.mark.asyncio
async def test_sale_discount_reduces_taxable_amount(client, tenant):
    gst = await _tax(client, tenant, "GST", 10.0)
    await _stack(client, tenant, gst)
    s = await _sell(client, tenant, [_SVC], discount=20)
    assert s["tax_total"] == pytest.approx(8.0)       # 10% of (100 − 20)
    assert s["total"] == pytest.approx(88.0)


@pytest.mark.asyncio
async def test_sale_without_taxes_unchanged(client, tenant):
    s = await _sell(client, tenant, [_SVC], discount=10)
    assert s["tax_total"] == 0 and s["total"] == pytest.approx(90.0)
