"""QA multi-shop 31 Oct 2026 (session C): every report and dashboard reads the same day the same way.

One known day: a job with parts + labour + 10% tax, a deposit and the final payment, a returned
part refunded, a counter sale and an expense. Revenue, receivables, expenses, COGS and low stock
must come out the same everywhere (services/revenue defines them); where a screen shows a
different measure on purpose (cash collected, jobs only), that figure is checked too.
"""
import uuid

from sqlalchemy import select

from backend.models import OrderLine, Part, Sale


async def _setup(client):
    email = f"rep-{uuid.uuid4().hex[:8]}@test.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Figures Co", "name": "Owner", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    H = {"Authorization": f"Bearer {r.json()['access_token']}"}
    locs = (await client.get("/api/staff/locations", headers=H)).json()
    loc = (locs if isinstance(locs, list) else locs["items"])[0]["id"]
    await client.post("/api/cash/accounts", json={"name": "Till", "payment_option": "cash"}, headers=H)
    acc = (await client.get("/api/cash/accounts", headers=H)).json()["items"][0]["id"]
    return H, loc, acc


async def _product(client, H, loc, name, price, cost, qty, min_stock=0, db=None):
    p = (await client.post("/api/catalog/products", json={
        "name": name, "sell_price": price, "cost_price": cost}, headers=H)).json()
    if qty:
        r = await client.post(f"/api/inventory/parts/{p['id']}/adjust", json={
            "location_id": loc, "qty": qty, "movement_type": "adjust"}, headers=H)
        assert r.status_code in (200, 201), r.text
    if min_stock:
        part = (await db.execute(select(Part).where(Part.id == p["id"]))).scalar_one()
        part.min_stock = min_stock
        await db.commit()
    return p["id"]


async def _status(client, H, group):
    sts = (await client.get("/api/statuses", headers=H)).json()
    for s in (sts.get("items", sts) if isinstance(sts, dict) else sts):
        if s.get("status_group") == group:
            return s["key"]


async def _known_day(client, db):
    H, loc, acc = await _setup(client)
    screen = await _product(client, H, loc, "Screen", 100, 40, 5)
    prot = await _product(client, H, loc, "Protector", 20, 5, 5)
    case = await _product(client, H, loc, "Case", 30, 8, 5)

    cid = (await client.post("/api/clients", json={"name": "Dana"}, headers=H)).json()["id"]
    o = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "screen"},
                           headers=H)).json()
    oid = o["id"]
    for body in ({"kind": "product", "line_type": "part", "ref_id": screen, "description": "Screen",
                  "qty": 1, "unit_price": 100, "cost_price": 40},
                 {"kind": "product", "line_type": "part", "ref_id": prot, "description": "Protector",
                  "qty": 1, "unit_price": 20, "cost_price": 5},
                 {"kind": "service", "line_type": "labour", "description": "Fitting", "qty": 1, "unit_price": 50}):
        r = await client.post(f"/api/orders/{oid}/lines", json={**body, "tax_rate": 10, "tax_mode": "imposed"},
                              headers=H)
        assert r.status_code in (200, 201), r.text
    # job: 110 + 22 + 55 = 187 with tax, 170 ex tax
    r = await client.post(f"/api/cash/orders/{oid}/prepay", json={"amount": 50, "account_id": acc}, headers=H)
    assert r.status_code == 201, r.text
    r = await client.post(f"/api/cash/orders/{oid}/pay", json={"method": "cash", "account_id": acc}, headers=H)
    assert r.status_code in (200, 201), r.text
    r = await client.patch(f"/api/orders/{oid}/status", json={"status": await _status(client, H, "closed")},
                           headers=H)
    assert r.status_code == 200, r.text
    # the protector comes back to the shelf and its 22 is refunded
    ret = (await client.post("/api/returns", json={"order_id": oid, "lines": [
        {"part_id": prot, "qty": 1, "restock": True}]}, headers=H)).json()
    r = await client.post("/api/returns/refunds", json={"account_id": acc, "amount": 22, "order_id": oid,
                                                        "return_id": ret["id"], "reason": "returned"}, headers=H)
    assert r.status_code == 201, r.text

    # a second job still open, 44 with tax, 10 paid → 34 owed
    o2 = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "battery"},
                            headers=H)).json()
    await client.post(f"/api/orders/{o2['id']}/lines", json={
        "kind": "service", "line_type": "labour", "description": "Diagnose", "qty": 1, "unit_price": 40,
        "tax_rate": 10, "tax_mode": "imposed"}, headers=H)
    await client.post(f"/api/cash/orders/{o2['id']}/prepay", json={"amount": 10, "account_id": acc}, headers=H)

    # a small job paid 25 on 20 and closed; the 5 overpaid goes back (not revenue, not an expense)
    o3 = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "clean"},
                            headers=H)).json()
    await client.post(f"/api/orders/{o3['id']}/lines", json={
        "kind": "service", "line_type": "labour", "description": "Clean", "qty": 1, "unit_price": 20}, headers=H)
    await client.post(f"/api/cash/orders/{o3['id']}/prepay", json={"amount": 25, "account_id": acc}, headers=H)
    r = await client.patch(f"/api/orders/{o3['id']}/status", json={"status": await _status(client, H, "closed")},
                           headers=H)
    assert r.status_code == 200, r.text
    r = await client.post(f"/api/cash/orders/{o3['id']}/refund", json={"amount": 5, "account_id": acc}, headers=H)
    assert r.status_code == 201, r.text

    # counter sale: one case, 30
    r = await client.post("/api/sales", json={"account_id": acc, "lines": [
        {"kind": "product", "ref_id": case, "description": "Case", "qty": 1, "unit_price": 30}]}, headers=H)
    assert r.status_code == 201, r.text
    # an operating expense
    r = await client.post("/api/finance/expenses", json={"description": "Rent", "amount": 12}, headers=H)
    assert r.status_code == 201, r.text

    # low stock: an active item under its minimum (counts), one with no minimum (doesn't),
    # and an archived one under its minimum (doesn't)
    await _product(client, H, loc, "Glue", 3, 1, 1, min_stock=4, db=db)
    await _product(client, H, loc, "Sticker", 1, 0, 0)
    old = await _product(client, H, loc, "Old cable", 5, 1, 0, min_stock=2, db=db)
    part = (await db.execute(select(Part).where(Part.id == old))).scalar_one()
    part.active = False
    await db.commit()

    lines = (await db.execute(select(OrderLine).where(OrderLine.order_id == oid))).scalars().all()
    job_cost = sum(float(ln.cost_price or 0) * ln.qty for ln in lines if ln.description == "Screen")
    sale_cost = float((await db.execute(select(Sale.total_cost))).scalars().first() or 0)
    return H, oid, job_cost, sale_cost


async def test_every_report_reads_the_known_day_the_same(client, db):
    H, oid, job_cost, sale_cost = await _known_day(client, db)
    REVENUE = 200.0          # job 170 ex tax − returned protector 20 ex tax + small job 20 + counter sale 30
    JOBS = 170.0
    OWED = 34.0
    COGS = round(job_cost + sale_cost, 2)   # the returned, restocked protector is no cost

    dash = (await client.get("/api/reports/dashboard", headers=H)).json()["stats"]
    summ = (await client.get("/api/finance/reports/summary", headers=H)).json()
    pnl = (await client.get("/api/finance/pnl", headers=H)).json()
    comp = (await client.get("/api/reports/company-dashboard", headers=H)).json()
    prof = (await client.get("/api/reports/job-profitability", headers=H)).json()

    got = {
        "dashboard.revenue": dash["revenue"],
        "finance_summary.total_revenue": summ["total_revenue"],
        "pnl.revenue": pnl["revenue"],
        "company.today.net_sales": comp["today"]["net_sales"],
        "company.monthly.net_sales": comp["monthly"]["net_sales"],
    }
    assert got == {k: REVENUE for k in got}, got
    assert pnl["tax_collected"] == 15.0          # 17 on the job − 2 on the refunded protector
    assert pnl["cogs"] == COGS, (pnl["cogs"], COGS)

    # jobs only, by close date: the same job figure
    assert prof["totals"]["revenue"] == JOBS, prof["totals"]
    assert prof["totals"]["parts_cost"] == round(job_cost, 2)

    # cash collected is its own, labelled figure: 50 + 137 + 10 + 25 + 30 in, 22 + 5 back
    assert comp["today"]["cash_collected"] == 225.0, comp["today"]
    assert comp["today"]["revenue"] == 252.0 and comp["today"]["refunds"] == 27.0

    owed = {
        "dashboard.outstanding": dash["outstanding"],
        "finance_summary.total_outstanding": summ["total_outstanding"],
        "company.kpis.receivables": comp["kpis"]["receivables"],
        "company.kpis.outstanding": comp["kpis"]["outstanding"],
    }
    assert owed == {k: OWED for k in owed}, owed

    # the refund is not an expense; the rent is
    assert summ["total_expenses"] == 12.0 and pnl["opex"] == 12.0
    flow = (await client.get("/api/reports/run/cash_flow", headers=H)).json()
    names = {row[0] for row in flow["rows"]}
    assert "expense" not in names, flow["rows"]

    # line reports: ex tax, net of the returned part, counter sales included
    q = "?date_from=2026-01-01&date_to=2100-01-01"
    sold = (await client.get(f"/api/reports/run/sold_products{q}", headers=H)).json()["rows"]
    services = (await client.get(f"/api/reports/run/provided_services{q}", headers=H)).json()["rows"]
    assert round(sum(r[2] for r in sold) + sum(r[2] for r in services), 2) == REVENUE, (sold, services)
    wo = (await client.get(f"/api/reports/run/wo_profit{q}", headers=H)).json()["rows"]
    assert round(sum(r[1] for r in wo), 2) == JOBS + 40.0, wo   # + the open job's 40 ex tax

    # low stock: one definition
    low = (await client.get("/api/reports/inventory/low-stock", headers=H)).json()
    feed = (await client.get("/api/reports/home-feed", headers=H)).json()["items"]
    feed_low = next((i["count"] for i in feed if i["key"] == "lowstock"), 0)
    assert [i["name"] for i in low["items"]] == ["Glue"], low
    assert dash["low_stock_parts"] == low["count"] == feed_low == 1


async def test_a_completed_job_counts_before_it_is_fully_paid(client, db):
    """QA shop 3: dashboard and finance summary said revenue 0.0 after money was collected,
    because only fully paid invoices counted."""
    H, loc, acc = await _setup(client)
    cid = (await client.post("/api/clients", json={"name": "Lee"}, headers=H)).json()["id"]
    o = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x"},
                           headers=H)).json()
    await client.post(f"/api/orders/{o['id']}/lines", json={
        "kind": "service", "line_type": "labour", "description": "Repair", "qty": 1, "unit_price": 80}, headers=H)
    await client.post(f"/api/cash/orders/{o['id']}/pay", json={"method": "cash", "account_id": acc, "amount": 30},
                      headers=H)
    r = await client.patch(f"/api/orders/{o['id']}/status", json={"status": await _status(client, H, "closed")},
                           headers=H)
    assert r.status_code == 200, r.text
    dash = (await client.get("/api/reports/dashboard", headers=H)).json()["stats"]
    summ = (await client.get("/api/finance/reports/summary", headers=H)).json()
    assert dash["revenue"] == summ["total_revenue"] == 80.0
    assert dash["outstanding"] == summ["total_outstanding"] == 50.0
