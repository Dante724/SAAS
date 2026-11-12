"""Audit probe: EXPLAIN QUERY PLAN (SQLite) for search and report statements."""
import json, os
import pytest
from sqlalchemy import event, text

OUT = os.environ["PROBE_OUT2"]

URLS = ["/api/orders?search=Probe", "/api/orders?search=415 555 0001", "/api/clients?search=5550",
        "/api/orders?limit=50&skip=0", "/api/reports/run/wo_created", "/api/reports/run/lead_sources",
        "/api/reports/insights/data?subject=payments&kind=trend&period=24m", "/api/reports/company-dashboard",
        "/api/finance/invoices", "/api/cash/transactions", "/api/inventory/movements"]


@pytest.mark.asyncio
async def test_explain(client, tenant, db):
    cid = (await client.post("/api/clients", json={"name": "Probe A", "phone": "+1 415 555 0001"}, headers=tenant)).json()["id"]
    await client.post("/api/orders", json={"client_id": cid}, headers=tenant)
    eng = db.bind.sync_engine
    out = {}
    for u in URLS:
        caught = []
        def hit(conn, cur, stmt, params, ctx, many):
            if stmt.lstrip().upper().startswith("SELECT"):
                caught.append((stmt, params))
        event.listen(eng, "before_cursor_execute", hit)
        await client.get(u, headers=tenant)
        event.remove(eng, "before_cursor_execute", hit)
        plans = []
        async with db.bind.connect() as conn:
            for stmt, params in caught:
                raw = await conn.get_raw_connection()
                cur = await raw.driver_connection.execute("EXPLAIN QUERY PLAN " + stmt, params)
                rows = await cur.fetchall()
                plans.append({"sql": " ".join(stmt.split())[:260], "plan": [r[-1] for r in rows]})
        out[u] = plans
    json.dump(out, open(OUT, "w"), indent=1)
