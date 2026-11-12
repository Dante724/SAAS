"""Audit probe (scratchpad only): per-endpoint SQL statement counts at two data sizes, bytes
returned, and captured SQL for selected report queries. Runs on throwaway SQLite."""
import json, os, re, time
import pytest
from sqlalchemy import event

OUT = os.environ["PROBE_OUT"]
PNG = "data:image/png;base64," + ("iVBORw0KGgo" + "A" * 200_000)   # ~200 KB fake photo payload


class Cap:
    def __init__(self, engine):
        self.engine, self.n, self.sql = engine, 0, []
    def _hit(self, conn, cur, stmt, params, ctx, many):
        self.n += 1
        self.sql.append(stmt)
    def __enter__(self):
        event.listen(self.engine, "before_cursor_execute", self._hit); return self
    def __exit__(self, *e):
        event.remove(self.engine, "before_cursor_execute", self._hit)


ENDPOINTS = [
    "/api/orders?limit=50", "/api/orders?limit=50&facets=true", "/api/orders?search=Probe", "/api/orders?search=5551",
    "/api/orders/board", "/api/clients?limit=50", "/api/clients?search=Probe",
    "/api/inventory/parts", "/api/catalog/products", "/api/finance/invoices", "/api/cash/transactions",
    "/api/payments/intents", "/api/sales", "/api/returns", "/api/finance/credit-notes",
    "/api/reports/dashboard", "/api/reports/company-dashboard", "/api/reports/home-feed",
    "/api/reports/job-profitability", "/api/reports/total-profit", "/api/finance/pnl",
    "/api/finance/reports/summary", "/api/analytics/business-insights", "/api/analytics/trends",
    "/api/analytics/technician-productivity", "/api/analytics/location-comparison",
    "/api/reports/insights/data?subject=payments&kind=trend&period=24m",
    "/api/reports/insights/data?subject=closed_orders", "/api/reports/insights/data?subject=sales",
    "/api/inventory/movements", "/api/inventory/reorder-list", "/api/inventory/to-order",
    "/api/tasks", "/api/leads", "/api/notifications/log", "/api/timeclock/entries",
    "/api/cash/summary", "/api/finance/reconciliation",
]
REPORT_KEYS = ["payroll", "wo_created", "labor_utilization", "labor_efficiency", "labor_by_job", "labor_cost",
               "wo_closed", "wo_profit", "lead_sources", "cancellations", "dropped_jobs", "technicians",
               "provided_services", "sold_products", "cash_flow", "total_money", "product_turnover",
               "inventory_writeoffs", "stock_levels", "stock_valuation", "assortment", "purchase_list",
               "reorder_forecast", "dead_stock", "expiration", "activity_log"]


async def _seed(client, h, n, state):
    _post, _patch = client.post, client.patch
    async def post(*a, **k):
        r = await _post(*a, **k); state.setdefault("codes", []).append((a[0].split("/")[2:4], r.status_code, r.text[:120] if r.status_code >= 300 else "")); return r
    async def patch(*a, **k):
        r = await _patch(*a, **k); state.setdefault("codes", []).append((a[0], r.status_code, r.text[:120] if r.status_code >= 300 else "")); return r
    loc = (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]
    if "acct" not in state:
        state["acct"] = (await post("/api/cash/accounts", json={"name": "Card", "payment_option": "card"},
                                           headers=h)).json()["id"]
        sts = (await client.get("/api/statuses", headers=h)).json()
        sts = sts.get("items", sts) if isinstance(sts, dict) else sts
        state["closed"] = next(s["key"] for s in sts if s.get("status_group") == "closed")
        r = await post("/api/suppliers", json={"name": "Sup"}, headers=h)
        state["sup"] = r.json().get("id") if r.status_code < 300 else None
    for i in range(n):
        k = state.setdefault("k", 0); state["k"] += 1
        pr = await post("/api/inventory/parts", headers=h, json={
            "name": f"Part {k}", "sku": f"SKU-{k}", "sell_price": 50, "cost_price": 20, "min_stock": 5,
            "image_data": PNG})
        pid = pr.json()["id"]
        await post(f"/api/inventory/parts/{pid}/adjust", headers=h,
                          json={"location_id": loc, "qty": 3, "movement_type": "receive"})
        cid = (await post("/api/clients", json={"name": f"Probe Client {k}", "phone": f"+1 415 555 {k:04d}"},
                                 headers=h)).json()["id"]
        oid = (await post("/api/orders", json={"client_id": cid, "location_id": loc}, headers=h)).json()["id"]
        await post(f"/api/orders/{oid}/lines", json={"line_type": "labour", "description": "Fix",
                                                           "qty": 1, "unit_price": 100}, headers=h)
        await post(f"/api/orders/{oid}/lines", json={"line_type": "part", "kind": "product", "ref_id": pid,
                                                           "description": f"Part {k}", "qty": 1, "unit_price": 50}, headers=h)
        await post(f"/api/cash/orders/{oid}/pay", json={"method": "card", "account_id": state["acct"],
                                                              "amount": 150}, headers=h)
        await post(f"/api/feed/order/{oid}", json={"item_type": "photo", "media_data": PNG,
                                                          "visibility": "client"}, headers=h)
        await patch(f"/api/orders/{oid}/status", json={"status": state["closed"]}, headers=h)
        if state.get("sup"):
            await post("/api/inventory/po", headers=h, json={"supplier_id": state["sup"], "location_id": loc, "lines": [{"part_id": pid, "qty_ordered": 5, "unit_cost": 20}]})
        await post("/api/sales", headers=h, json={"lines": [{"kind": "product", "ref_id": pid,
                          "description": f"Part {k}", "qty": 1, "unit_price": 50}], "payment_method": "cash"})


async def _measure(client, h, engine):
    res = {}
    urls = ENDPOINTS + [f"/api/reports/run/{k}" for k in REPORT_KEYS]
    for u in urls:
        with Cap(engine) as c:
            t = time.perf_counter()
            r = await client.get(u, headers=h)
            ms = (time.perf_counter() - t) * 1000
        res[u] = {"status": r.status_code, "queries": c.n, "bytes": len(r.content), "ms": round(ms, 1)}
    return res


@pytest.mark.asyncio
async def test_probe(client, tenant, db):
    eng = db.bind.sync_engine
    state = {}
    await _seed(client, tenant, 3, state)
    small = await _measure(client, tenant, eng)
    await _seed(client, tenant, 12, state)
    big = await _measure(client, tenant, eng)
    # SQL capture for the Home dashboard and the profit report
    caps = {}
    for u in ["/api/reports/company-dashboard", "/api/reports/run/wo_profit", "/api/reports/run/stock_levels",
              "/api/reports/run/activity_log", "/api/reports/insights/data?subject=payments&kind=trend&period=24m",
              "/api/orders?search=Probe", "/api/clients?search=5550"]:
        with Cap(eng) as c:
            await client.get(u, headers=tenant)
        caps[u] = c.sql
    bad=[c for c in state["codes"] if c[1]>=300]
    json.dump({"seed_errors": bad[:40], "n_seed": len(state["codes"]), "small": small, "big": big, "sql": caps}, open(OUT, "w"), indent=1, default=str)
