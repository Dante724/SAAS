"""QA 2026-10-31 (B, med): invoice-preview lines ignored line discounts (and tax), so the
lines didn't add up to the total shown under them."""
import pytest


@pytest.mark.asyncio
async def test_invoice_preview_lines_add_up_to_the_total(client, tenant):
    cid = (await client.post("/api/clients", json={"name": "C", "phone": "+15559"}, headers=tenant)).json()["id"]
    oid = (await client.post("/api/orders", json={"client_id": cid}, headers=tenant)).json()["id"]
    for body in (
        {"line_type": "labour", "description": "Fix", "qty": 1, "unit_price": 100, "discount": 10},
        {"line_type": "part", "description": "Screen", "qty": 2, "unit_price": 50,
         "discount": 15, "discount_type": "fixed", "tax_rate": 13},
    ):
        r = await client.post(f"/api/orders/{oid}/lines", headers=tenant, json=body)
        assert r.status_code in (200, 201), r.text
    p = (await client.get(f"/api/orders/{oid}/invoice-preview", headers=tenant)).json()
    by = {l["description"]: l for l in p["lines"]}
    assert by["Fix"]["total"] == pytest.approx(90.0)
    assert by["Screen"]["total"] == pytest.approx(96.05)        # (100 - 15) × 1.13
    assert by["Fix"]["discount"] == 10
    assert sum(l["total"] for l in p["lines"]) == pytest.approx(p["total"])
