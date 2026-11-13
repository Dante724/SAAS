"""Re-review money N3 (cashbox restriction leaks through Reports, the dashboard and the accountant
export) and the SEC-02 void gap (voiding a sale or refund moved another cashbox's balance)."""
from backend.tests.test_p1_money_audit import _acct, _balance, _income, _job, _staff


async def test_restricted_role_reports_show_only_their_cashboxes(client, tenant, db):
    till, safe = await _acct(client, tenant, "Front till"), await _acct(client, tenant, "Safe")
    await _income(client, tenant, till, 50)
    await _income(client, tenant, safe, 500)
    s = await _staff(client, tenant, account_ids=[till])

    tm = await client.get("/api/reports/run/total_money", headers=s)
    assert tm.status_code == 200, tm.text
    names = [row[0] for row in tm.json()["rows"]]
    assert "Safe" not in names and "Front till" in names, names

    cf = await client.get("/api/reports/run/cash_flow", headers=s)
    assert cf.status_code == 200, cf.text
    assert sum(row[1] for row in cf.json()["rows"]) == 50, cf.json()

    acc = await client.get("/api/finance/export/accountant.csv", headers=s)
    assert acc.status_code == 200, acc.text
    assert "Safe" not in acc.text and "Front till" in acc.text

    dash = await client.get("/api/reports/company-dashboard", headers=s)
    assert dash.status_code == 200, dash.text
    d = dash.json()["kpis"]
    assert (d["cash"], d["total_money"]) == (50, 50), {k: d[k] for k in ("cash", "cashless", "total_money")}

async def test_restricted_role_cannot_void_a_sale_paid_into_another_cashbox(client, tenant, db):
    till, safe = await _acct(client, tenant, "Front till"), await _acct(client, tenant, "Safe")
    r = await client.post("/api/sales", json={"account_id": safe, "lines": [
        {"kind": "service", "description": "Fit", "qty": 1, "unit_price": 80}]}, headers=tenant)
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    s = await _staff(client, tenant, account_ids=[till])
    r = await client.delete(f"/api/sales/{sid}?reason=oops", headers=s)
    assert r.status_code == 403, r.text
    assert await _balance(db, safe) == 80.0


async def test_restricted_role_cannot_void_a_refund_paid_from_another_cashbox(client, tenant, db):
    till, safe = await _acct(client, tenant, "Front till"), await _acct(client, tenant, "Safe")
    oid = await _job(client, tenant)
    r = await client.post(f"/api/cash/orders/{oid}/pay", json={"method": "cash", "account_id": safe,
                                                               "amount": 100}, headers=tenant)
    assert r.status_code == 200, r.text
    r = await client.post("/api/returns/refunds", json={"account_id": safe, "amount": 30, "order_id": oid},
                          headers=tenant)
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    s = await _staff(client, tenant, account_ids=[till])
    r = await client.delete(f"/api/returns/refunds/{rid}?reason=mistake", headers=s)
    assert r.status_code == 403, r.text
    assert await _balance(db, safe) == 70.0
