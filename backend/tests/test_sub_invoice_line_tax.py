"""Hardening council M11 — an invoice billing a subset of a job's lines (or "bill the rest")
carried tax 0: new_invoice only took the job's tax when it billed whole jobs, so a per-line
invoice of a taxed job exported as zero-rated and its subtotal equalled its total."""
import pytest

LINE = {"line_type": "labour", "kind": "custom", "description": "Work", "unit_price": 100, "qty": 1}


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


async def _job(client, h, loc, lines):
    cid = (await client.post("/api/clients", json={"name": "Cust"}, headers=h)).json()["id"]
    r = await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x",
                                               "is_estimate": False, "lines": []}, headers=h)
    assert r.status_code in (200, 201), r.text
    oid = r.json()["id"]
    for ln in lines:
        r = await client.post(f"/api/orders/{oid}/lines", json={**LINE, **ln}, headers=h)
        assert r.status_code in (200, 201), r.text
    return oid, [ln["id"] for ln in (await client.get(f"/api/orders/{oid}", headers=h)).json()["lines"]]


async def _sub_invoice(client, h, oid, line_ids):
    r = await client.post(f"/api/cash/orders/{oid}/invoices", json={"line_ids": line_ids}, headers=h)
    assert r.status_code == 201, r.text
    return (await client.get(f"/api/finance/invoices/{r.json()['invoice_id']}", headers=h)).json()


@pytest.mark.asyncio
async def test_sub_invoice_keeps_line_tax(client, tenant):
    loc = await _loc(client, tenant)
    oid, lids = await _job(client, tenant, loc, [{"tax_rate": 10, "tax_mode": "imposed"}])
    inv = await _sub_invoice(client, tenant, oid, lids)
    assert inv["total"] == 110
    assert inv["tax"] == pytest.approx(10), inv
    assert inv["subtotal"] == pytest.approx(100), inv


@pytest.mark.asyncio
async def test_bill_the_rest_keeps_only_the_remaining_lines_tax(client, tenant):
    """Two taxed lines, the first billed on its own; "bill the rest" (no line_ids) carries the
    second line's tax only, and an included-VAT line's tax is the part inside its price."""
    loc = await _loc(client, tenant)
    oid, lids = await _job(client, tenant, loc, [
        {"tax_rate": 10, "tax_mode": "imposed"},                       # 100 + 10
        {"unit_price": 120, "tax_rate": 20, "tax_mode": "included"},   # 120 incl. 20
    ])
    first = await _sub_invoice(client, tenant, oid, [lids[0]])
    assert (first["total"], first["tax"]) == (110, pytest.approx(10)), first
    rest = await _sub_invoice(client, tenant, oid, [])
    assert rest["total"] == 120
    assert rest["tax"] == pytest.approx(20), rest
    assert rest["subtotal"] == pytest.approx(100), rest
