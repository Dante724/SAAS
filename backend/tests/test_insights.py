"""Build 144ay — Company Insights: meta, live diagram/trend aggregation, saved cards."""
import pytest


async def _loc(client, H):
    d = (await client.get("/api/staff/locations", headers=H)).json()
    return (d if isinstance(d, list) else d["items"])[0]["id"]


async def test_insights_meta(client, tenant):
    m = (await client.get("/api/reports/insights/meta", headers=tenant)).json()
    keys = [s["key"] for s in m["subjects"]]
    assert "closed_orders" in keys and "sales" in keys and "payments" in keys
    assert m["periods_trend"] and m["periods_diagram"]
    # sales exposes gross profit
    sales = next(s for s in m["subjects"] if s["key"] == "sales")
    assert "gross_profit" in [x["key"] for x in sales["metrics"]]


async def test_insights_diagram_and_trend_from_sales(client, tenant):
    await _loc(client, tenant)
    await client.post("/api/clients", json={"name": "Bo"}, headers=tenant)
    # closed_orders diagram should return a well-formed shape even with no closed orders
    d = (await client.get("/api/reports/insights/data?kind=diagram&subject=closed_orders&metric=amount&group=type&period=30d", headers=tenant)).json()
    assert d["kind"] == "diagram" and "labels" in d and "values" in d and "table" in d
    assert d["group"] == "type"
    # trend shape: 30 daily buckets
    t = (await client.get("/api/reports/insights/data?kind=trend&subject=payments&metric=amount&period=30d", headers=tenant)).json()
    assert t["kind"] == "trend" and len(t["labels"]) == 30 and len(t["values"]) == 30


async def test_insights_cards_crud(client, tenant):
    r = await client.post("/api/reports/insights/cards", json={
        "kind": "diagram", "subject": "sales", "metric": "gross_profit", "group_by": "seller", "period": "30d", "view": "chart"}, headers=tenant)
    assert r.status_code == 201
    cid = r.json()["id"]
    items = (await client.get("/api/reports/insights/cards", headers=tenant)).json()["items"]
    assert any(c["id"] == cid and c["subject"] == "sales" for c in items)
    # reorder + delete
    assert (await client.patch("/api/reports/insights/cards/reorder", json={"ordered_ids": [cid]}, headers=tenant)).status_code == 200
    assert (await client.request("DELETE", f"/api/reports/insights/cards/{cid}", headers=tenant)).status_code == 200
    assert (await client.get("/api/reports/insights/cards", headers=tenant)).json()["items"] == []


async def test_insights_bad_subject_rejected(client, tenant):
    assert (await client.get("/api/reports/insights/data?subject=nope", headers=tenant)).status_code == 400
    assert (await client.post("/api/reports/insights/cards", json={"subject": "nope"}, headers=tenant)).status_code == 400
