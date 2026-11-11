"""QA wave 3, W3-B (5-shop re-run on Postgres, 1 Nov 2026): money reports after till returns.

One fixture day, 10% tax on jobs and at the till:
  job A      Screen 100 + Fitting 50, paid 165, closed            → 150 ex tax, 15 tax, cost 40
  sale S1    2 Chargers @30 + Setup 20 = 88; one charger returned → 50, tax 5, cost 10
  sale S2    a Case @50 = 55, returned in full                    → 0
  sale S3    two unnamed items, 7 and 9 = 17.60                   → 16, tax 1.60
  sale S4    a Charger, deleted (voided)                          → nothing
  job B      Repair 40, paid 44, closed; 11 refunded; a 5 refund made and deleted → 30, tax 3
  job C      Repair 110, 33 deposit, dropped                      → keeps 30, tax 3
  job D      Repair 66, paid, never finished, deleted (money back) → nothing, owes nothing
  job E      a Screen added and removed                           → nothing

Revenue 276, tax 27.60, cost of goods 50. Cash: 468.60 in, 165 back, 303.60 in the till.
Every report and dashboard must read that day the same way.
"""
import csv
import io

import pytest
from sqlalchemy import select

from backend.models import Part
from backend.tests.shop_day import shop_today_for
from backend.tests.test_b5_pos_quote import _sales_tax
from backend.tests.test_report_figures_agree import _product, _setup, _status, _total_profit

RANGE = "?date_from=2026-01-01&date_to=2100-01-01"
REVENUE, TAX, COGS = 276.0, 27.6, 50.0
CASH_IN, CASH_BACK = 468.6, 165.0


async def _job(client, H, loc, cid, lines):
    o = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x"},
                           headers=H)).json()
    for body in lines:
        r = await client.post(f"/api/orders/{o['id']}/lines", json=body, headers=H)
        assert r.status_code in (200, 201), r.text
    return o["id"]


async def _pay_all(client, H, oid, acc):
    r = await client.post(f"/api/cash/orders/{oid}/pay", json={"method": "cash", "account_id": acc}, headers=H)
    assert r.status_code in (200, 201), r.text


async def _close(client, H, oid):
    r = await client.patch(f"/api/orders/{oid}/status", json={"status": await _status(client, H, "closed")},
                           headers=H)
    assert r.status_code == 200, r.text


async def _sale(client, H, acc, lines):
    r = await client.post("/api/sales", json={"account_id": acc, "lines": lines}, headers=H)
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    return sid, (await client.get(f"/api/sales/{sid}", headers=H)).json()


async def _return(client, H, sid, acc, lines):
    r = await client.post(f"/api/sales/{sid}/returns", json={"account_id": acc, "lines": lines}, headers=H)
    assert r.status_code == 201, r.text


async def _fixture_day(client, db):
    H, loc, acc = await _setup(client)
    await _sales_tax(client, H, 10)
    charger = await _product(client, H, loc, "Charger", 30, 10, 10)
    case = await _product(client, H, loc, "Case", 50, 20, 5)
    screen = await _product(client, H, loc, "Screen", 100, 40, 5)
    cid = (await client.post("/api/clients", json={"name": "Dana"}, headers=H)).json()["id"]
    gina = (await client.post("/api/clients", json={"name": "Gina", "source": "Google"}, headers=H)).json()["id"]
    tax = {"tax_rate": 10, "tax_mode": "imposed"}

    def repair(price, **kw):
        return {"kind": "service", "line_type": "labour", "description": "Repair", "qty": 1, "unit_price": price, **kw}

    # job A
    a = await _job(client, H, loc, cid, [
        {"kind": "product", "line_type": "part", "ref_id": screen, "description": "Screen", "qty": 1,
         "unit_price": 100, "cost_price": 40, **tax},
        {"kind": "service", "line_type": "labour", "description": "Fitting", "qty": 1, "unit_price": 50, **tax}])
    await _pay_all(client, H, a, acc)
    await _close(client, H, a)

    # S1: one charger of two comes back
    s1, sale = await _sale(client, H, acc, [
        {"kind": "product", "ref_id": charger, "description": "Charger", "qty": 2, "unit_price": 30},
        {"kind": "service", "description": "Setup", "qty": 1, "unit_price": 20}])
    assert sale["total"] == pytest.approx(88)
    ch_line = next(ln for ln in sale["lines"] if ln["description"] == "Charger")
    await _return(client, H, s1, acc, [{"line_id": ch_line["id"], "qty": 1}])
    # S2: returned in full
    s2, sale = await _sale(client, H, acc, [
        {"kind": "product", "ref_id": case, "description": "Case", "qty": 1, "unit_price": 50}])
    await _return(client, H, s2, acc, [{"line_id": sale["lines"][0]["id"], "qty": 1}])
    # S3: two different unnamed items
    await _sale(client, H, acc, [{"kind": "product", "description": "", "qty": 1, "unit_price": 7},
                                 {"kind": "product", "description": "", "qty": 1, "unit_price": 9}])
    # S4: deleted
    s4, _ = await _sale(client, H, acc, [
        {"kind": "product", "ref_id": charger, "description": "Charger", "qty": 1, "unit_price": 30}])
    r = await client.delete(f"/api/sales/{s4}", headers=H)
    assert r.status_code == 200, r.text

    # job B: 11 back for good will; a 5 refund made by mistake and deleted
    b = await _job(client, H, loc, gina, [repair(40, **tax)])
    await _pay_all(client, H, b, acc)
    await _close(client, H, b)
    r = await client.post("/api/returns/refunds", json={"account_id": acc, "amount": 11, "order_id": b,
                                                        "reason": "goodwill"}, headers=H)
    assert r.status_code == 201, r.text
    r = await client.post("/api/returns/refunds", json={"account_id": acc, "amount": 5, "order_id": b,
                                                        "reason": "typo"}, headers=H)
    assert r.status_code == 201, r.text
    r = await client.delete(f"/api/returns/refunds/{r.json()['id']}", headers=H)
    assert r.status_code == 200, r.text

    # job C: dropped after a 30 deposit
    c = await _job(client, H, loc, cid, [repair(100, **tax)])
    r = await client.post(f"/api/cash/orders/{c}/prepay", json={"amount": 33, "account_id": acc}, headers=H)
    assert r.status_code == 201, r.text
    r = await client.patch(f"/api/orders/{c}/status",
                           json={"status": await _status(client, H, "dropped"), "reason": "Never came back"},
                           headers=H)
    assert r.status_code == 200, r.text

    # job D: paid, then deleted with the money handed back
    d = await _job(client, H, loc, cid, [repair(60, **tax)])
    await _pay_all(client, H, d, acc)
    r = await client.delete(f"/api/orders/{d}?reason=duplicate", headers=H)
    assert r.status_code == 200, r.text

    # job E: a screen added by mistake and removed
    e = await _job(client, H, loc, cid, [
        {"kind": "product", "line_type": "part", "ref_id": screen, "description": "Screen", "qty": 1,
         "unit_price": 100, "cost_price": 40}])
    lines = (await client.get(f"/api/orders/{e}", headers=H)).json()["lines"]
    r = await client.delete(f"/api/orders/{e}/lines/{lines[0]['id']}", headers=H)
    assert r.status_code in (200, 204), r.text

    from backend.routers.reports import _dash_cache
    _dash_cache.clear()
    return H, acc, {"charger": charger, "case": case, "screen": screen}


async def _run(client, H, key, extra=""):
    r = await client.get(f"/api/reports/run/{key}{RANGE}{extra}", headers=H)
    assert r.status_code == 200, r.text
    return r.json()["rows"]


async def _till_balance(client, H):
    return round(sum(a["balance"] for a in (await client.get("/api/cash/accounts", headers=H)).json()["items"]), 2)


@pytest.mark.asyncio
async def test_every_money_report_reads_the_fixture_day_the_same(client, db):
    H, acc, _ = await _fixture_day(client, db)
    pnl = (await client.get("/api/finance/pnl", headers=H)).json()
    summ = (await client.get("/api/finance/reports/summary", headers=H)).json()
    dash = (await client.get("/api/reports/dashboard", headers=H)).json()["stats"]
    comp = (await client.get("/api/reports/company-dashboard", headers=H)).json()
    tp = await _total_profit(client, H)

    revenue = {"pnl": pnl["revenue"], "summary": summ["total_revenue"], "dashboard": dash["revenue"],
               "company.today": comp["today"]["net_sales"], "company.month": comp["monthly"]["net_sales"],
               "total_profit": tp["totals"]["revenue"]}
    assert revenue == {k: REVENUE for k in revenue}, (revenue, tp["buckets"])
    assert pnl["tax_collected"] == pytest.approx(TAX) and tp["totals"]["tax"] == pytest.approx(TAX), (pnl, tp["totals"])
    assert pnl["cogs"] == pytest.approx(COGS) and tp["totals"]["cogs"] == pytest.approx(COGS), (pnl, tp["totals"])
    # the till sales' own rows carry their returns: 50 + 0 + 16, tax 5 + 0 + 1.60, cost 10 + 0
    sales = tp["buckets"]["sales"]
    assert (sales["revenue"], sales["tax"], sales["cogs"]) == pytest.approx((66, 6.6, 10)), tp["buckets"]

    # nothing is owed: everything is paid, the dropped job's rest is written off, job D was deleted
    owed = {"dashboard": dash["outstanding"], "summary": summ["total_outstanding"],
            "company": comp["kpis"]["receivables"]}
    assert owed == {k: 0.0 for k in owed}, owed


@pytest.mark.asyncio
async def test_total_profit_csv_matches_the_page_and_dates_the_other_row_in_range(client, db):
    from backend.services.dates import now_utc
    H, acc, _ = await _fixture_day(client, db)
    r = await client.get("/api/reports/total-profit.csv?date_from=2026-01-01", headers=H)
    assert r.status_code == 200, r.text
    rows = list(csv.reader(io.StringIO(r.text)))
    head, total = rows[0], rows[-1]
    assert total[0] == "Total"
    got = {k: float(total[head.index(k)]) for k in ("Sales", "Tax", "Parts cost")}
    assert got == pytest.approx({"Sales": REVENUE, "Tax": TAX, "Parts cost": COGS}), got
    today = (await shop_today_for(client, H)).isoformat()
    for row in rows[1:-1]:
        assert row[2] <= today, row          # QA N17: "Refunds and other" was dated tomorrow


@pytest.mark.asyncio
async def test_company_dashboard_cash_includes_till_refunds_and_matches_the_tills(client, db):
    H, acc, _ = await _fixture_day(client, db)
    comp = (await client.get("/api/reports/company-dashboard", headers=H)).json()
    till = await _till_balance(client, H)
    assert till == pytest.approx(CASH_IN - CASH_BACK)
    assert comp["today"]["paid"] == pytest.approx(CASH_IN), comp["today"]
    assert comp["today"]["refunds"] == pytest.approx(CASH_BACK), comp["today"]
    assert comp["today"]["cash_collected"] == pytest.approx(till), comp["today"]
    assert comp["monthly"]["cash_collected"] == pytest.approx(till), comp["monthly"]


@pytest.mark.asyncio
async def test_cash_flow_leaves_out_deleted_sales_and_refunds(client, db):
    H, acc, _ = await _fixture_day(client, db)
    flow = await _run(client, H, "cash_flow")
    assert round(sum(r[1] for r in flow), 2) == pytest.approx(await _till_balance(client, H)), flow


@pytest.mark.asyncio
async def test_lead_sources_paid_is_net_of_refunds(client, db):
    H, acc, _ = await _fixture_day(client, db)
    rows = await _run(client, H, "lead_sources")
    google = next(r for r in rows if r[0] == "Google")
    assert google[4] == pytest.approx(44 - 11), rows       # the deleted 5 refund never happened


@pytest.mark.asyncio
async def test_sold_products_are_ex_tax_net_of_returns_and_never_merge_unnamed_items(client, db):
    H, acc, _ = await _fixture_day(client, db)
    sold = await _run(client, H, "sold_products")
    by = {}
    for r in sold:
        by.setdefault(r[0], []).append(r)
    # one charger kept of S1 (S4 was deleted): ex tax 30, cost 10
    (ch,) = by["Charger"]
    assert (ch[1], ch[2], ch[3]) == pytest.approx((1, 30, 10)), sold
    assert all(r[1] == 0 and r[2] == 0 for r in by.get("Case", [])), sold
    unnamed = sorted(r[2] for name, rs in by.items() if name not in ("Charger", "Case", "Screen") for r in rs)
    assert unnamed == pytest.approx([7, 9]), sold                  # two items, never one "" row of 16
    services = await _run(client, H, "provided_services")
    setup = next(r for r in services if r[0] == "Setup")
    assert setup[2] == pytest.approx(20), services                  # ex tax (22 with it)


@pytest.mark.asyncio
async def test_insights_use_the_revenue_rule(client, db):
    H, acc, _ = await _fixture_day(client, db)
    bi = (await client.get("/api/analytics/business-insights?days=30", headers=H)).json()
    assert bi["revenue"] == pytest.approx(REVENUE), bi
    assert bi["orders_closed"] == 2, bi                              # A and B: not the dropped or deleted job
    jobs = (await client.get("/api/reports/insights/data?kind=diagram&subject=closed_orders&metric=amount"
                             "&group=status_group&period=30d", headers=H)).json()
    sales = (await client.get("/api/reports/insights/data?kind=diagram&subject=sales&metric=amount"
                              "&group=location&period=30d", headers=H)).json()
    assert dict(zip(jobs["labels"], jobs["values"])) == pytest.approx({"closed": 180, "dropped": 30}), jobs
    assert sum(sales["values"]) == pytest.approx(66), sales
    assert sum(jobs["values"]) + sum(sales["values"]) == pytest.approx(REVENUE)


@pytest.mark.asyncio
async def test_product_turnover_counts_only_what_stayed_used(client, db):
    H, acc, _ = await _fixture_day(client, db)
    rows = {r[0]: r[2] for r in await _run(client, H, "product_turnover")}
    # Screen: job A's (job E's was removed); Charger: S1 kept one (S4 deleted); Case: returned
    assert rows.get("Screen") == 1 and rows.get("Charger") == 1, rows
    assert not rows.get("Case"), rows


@pytest.mark.asyncio
async def test_stock_value_reports_use_one_cost_basis(client, db):
    H, acc, ids = await _fixture_day(client, db)
    part = (await db.execute(select(Part).where(Part.id == ids["charger"]))).scalar_one()
    part.avg_cost = 12.5                           # bought at different prices since the list cost was set
    await db.commit()
    levels = await _run(client, H, "stock_levels")
    valuation = await _run(client, H, "stock_valuation")
    assortment = await _run(client, H, "assortment")
    total = next(r for r in valuation if r[0] == "TOTAL")[3]
    assert round(sum(r[5] for r in levels), 2) == pytest.approx(total), (levels, valuation)
    assert round(sum(r[7] for r in assortment), 2) == pytest.approx(total), (assortment, valuation)
    charger = next(r for r in levels if r[0] == "Charger")
    assert charger[5] == pytest.approx(charger[2] * 12.5), charger


@pytest.mark.asyncio
async def test_a_deleted_paid_job_never_turns_reports_negative(client, db):
    """QA shop 4 N4: after a paid job was deleted (its money handed back), revenue went negative
    and the job was still owed. A deleted job is out of every report, its refund with it."""
    H, loc, acc = await _setup(client)
    cid = (await client.post("/api/clients", json={"name": "Hal"}, headers=H)).json()["id"]
    oid = await _job(client, H, loc, cid, [{"kind": "service", "line_type": "labour", "description": "Repair",
                                            "qty": 1, "unit_price": 80, "tax_rate": 10, "tax_mode": "imposed"}])
    await _pay_all(client, H, oid, acc)
    r = await client.delete(f"/api/orders/{oid}?reason=mistake", headers=H)
    assert r.status_code == 200, r.text
    from backend.routers.reports import _dash_cache
    _dash_cache.clear()
    pnl = (await client.get("/api/finance/pnl", headers=H)).json()
    dash = (await client.get("/api/reports/dashboard", headers=H)).json()["stats"]
    comp = (await client.get("/api/reports/company-dashboard", headers=H)).json()
    tp = await _total_profit(client, H)
    assert (pnl["revenue"], pnl["tax_collected"], dash["revenue"], comp["today"]["net_sales"],
            tp["totals"]["revenue"], tp["totals"]["tax"]) == (0, 0, 0, 0, 0, 0), (pnl, tp["buckets"])
    assert dash["outstanding"] == 0 and comp["kpis"]["receivables"] == 0
    techs = await _run(client, H, "technicians")
    assert all(r[2] == 0 for r in techs), techs
