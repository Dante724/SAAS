"""Build 144ed — free-form catalog/manual lines on the invoice itself, plus
multi-order at create and the comment/due fields (New Invoice drawer parity).
"""
import uuid

import pytest


async def _register(client):
    email = f"a-{uuid.uuid4().hex[:8]}@t.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Co", "name": "Admin", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _order(client, H, complaint, lines):
    loc = (await client.get("/api/staff/locations", headers=H)).json()
    loc_id = (loc[0]["id"] if isinstance(loc, list) else loc["items"][0]["id"])
    o = (await client.post("/api/orders", json={"location_id": loc_id, "complaint": complaint}, headers=H)).json()
    for d, p in lines:
        await client.post(f"/api/orders/{o['id']}/lines", json={
            "line_type": "labour", "kind": "service", "description": d, "unit_price": p}, headers=H)
    return o


@pytest.mark.asyncio
async def test_create_with_free_lines_only(client):
    H = await _register(client)
    inv = (await client.post("/api/finance/invoices", json={
        "display_mode": "detailed",
        "comment": "thanks",
        "lines": [
            {"kind": "service", "description": "Diagnostics", "qty": 1, "unit_price": 50},
            {"kind": "custom", "description": "Rush fee", "qty": 2, "unit_price": 10},
        ],
    }, headers=H)).json()
    body = (await client.get(f"/api/finance/invoices/{inv['id']}", headers=H)).json()
    # subtotal auto-summed from the free lines (50 + 20)
    assert body["subtotal"] == 70
    assert body["comment"] == "thanks"
    descs = [l["description"] for l in body["lines"]]
    assert "Diagnostics" in descs and "Rush fee" in descs
    # the rush-fee line carries qty 2 → total 20
    rush = next(l for l in body["lines"] if l["description"] == "Rush fee")
    assert rush["total"] == 20


@pytest.mark.asyncio
async def test_free_lines_fold_into_all_modes(client):
    H = await _register(client)
    o = await _order(client, H, "Screen", [("Order diag", 40)])
    inv = (await client.post("/api/finance/invoices", json={
        "order_ids": [o["id"]],
        "lines": [{"kind": "custom", "description": "Extra part", "qty": 1, "unit_price": 15}],
    }, headers=H)).json()
    body = (await client.get(f"/api/finance/invoices/{inv['id']}", headers=H)).json()
    # detailed = 1 order line + 1 free line
    assert len(body["lines"]) == 2
    assert body["subtotal"] == 55  # 40 order + 15 free
    # simplified = 1 per-order line + the free line
    simp = (await client.patch(f"/api/finance/invoices/{inv['id']}",
            json={"display_mode": "simplified"}, headers=H)).json()
    assert len(simp["lines"]) == 2
    # one_line = a single rolled-up line
    one = (await client.patch(f"/api/finance/invoices/{inv['id']}",
           json={"display_mode": "one_line"}, headers=H)).json()
    assert len(one["lines"]) == 1
    assert one["lines"][0]["total"] == 55


@pytest.mark.asyncio
async def test_create_with_multiple_orders(client):
    H = await _register(client)
    o1 = await _order(client, H, "A", [("L1", 10)])
    o2 = await _order(client, H, "B", [("L2", 20)])
    inv = (await client.post("/api/finance/invoices", json={
        "order_ids": [o1["id"], o2["id"]], "display_mode": "detailed"}, headers=H)).json()
    body = (await client.get(f"/api/finance/invoices/{inv['id']}", headers=H)).json()
    assert {x["id"] for x in body["orders"]} == {o1["id"], o2["id"]}
    assert len(body["lines"]) == 2
    assert body["subtotal"] == 30


@pytest.mark.asyncio
async def test_foreign_orders_are_dropped(client):
    H1 = await _register(client)
    o1 = await _order(client, H1, "A", [("L1", 10)])
    H2 = await _register(client)
    # H2 tries to bill H1's order at create — it must be silently dropped, not billed
    inv = (await client.post("/api/finance/invoices", json={
        "order_ids": [o1["id"]],
        "lines": [{"kind": "custom", "description": "Only mine", "qty": 1, "unit_price": 5}],
    }, headers=H2)).json()
    body = (await client.get(f"/api/finance/invoices/{inv['id']}", headers=H2)).json()
    assert body["orders"] == []
    assert body["subtotal"] == 5
