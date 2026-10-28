"""POS sale profit leaves out the tax collected: the shop owes it on, it isn't earned.

Release/1.0 stores the tax on each sale (launch B4); this checks the profit figures use it."""
import pytest

from backend.tests.test_launch_stream_a import _signup

pytestmark = pytest.mark.asyncio


async def _sale(client, H, price):
    r = await client.post("/api/sales", json={"lines": [
        {"kind": "service", "description": "Fitting", "qty": 1, "unit_price": price}]}, headers=H)
    assert r.status_code == 201, r.text
    return r.json()


async def test_imposed_tax_is_not_profit(client):
    H, _ = await _signup(client, country="CA")         # GST 5% added on top
    s = await _sale(client, H, 100)
    assert (s["total"], s["tax"], s["profit"]) == (105.0, 5.0, 100.0)
    row = next(x for x in (await client.get("/api/sales", headers=H)).json()["items"] if x["id"] == s["id"])
    assert row["profit"] == 100.0
    d = (await client.get("/api/reports/insights/data?kind=diagram&subject=sales&metric=gross_profit"
                          "&group=seller&period=30d", headers=H)).json()
    assert sum(r["value"] for r in d["table"]) == 100.0


async def test_included_vat_is_not_profit(client):
    H, _ = await _signup(client, country="GB")         # VAT 20% inside the price
    s = await _sale(client, H, 120)
    assert (s["total"], s["tax"], s["profit"]) == (120.0, 20.0, 100.0)
