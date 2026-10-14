"""144bk — accountant export: a clean CSV of the period's cash transactions so the owner never
re-keys into QuickBooks. Gated by can_export_accountant (role toggle)."""
import pytest


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


@pytest.mark.asyncio
async def test_accountant_export_csv(client, tenant):
    loc = await _loc(client, tenant)
    acc = (await client.post("/api/cash/accounts",
           json={"name": "Till 144bk", "payment_option": "cash", "location_id": loc}, headers=tenant)).json()
    await client.post("/api/cash/transactions",
                      json={"account_id": acc["id"], "txn_type": "income", "amount": 1000, "comment": "Sale A"}, headers=tenant)
    await client.post("/api/cash/transactions",
                      json={"account_id": acc["id"], "txn_type": "expense", "amount": 200, "comment": "Courier"}, headers=tenant)

    r = await client.get("/api/finance/export/accountant.csv", headers=tenant)
    assert r.status_code == 200, r.text
    assert "text/csv" in r.headers["content-type"]
    body = r.text
    assert "Date" in body and "Account" in body and "Income" in body and "Expense" in body  # header
    assert "Sale A" in body and "Courier" in body
    assert "1000" in body and "200" in body


@pytest.mark.asyncio
async def test_export_includes_business_expenses(client, tenant):
    # business expenses (rent/utilities) live in the Expense model, not the cashbox — the accountant
    # pack must still include them or the books are incomplete.
    await client.post("/api/finance/expenses", json={
        "category": "Rent", "description": "Shop rent Oct", "amount": 1500}, headers=tenant)
    r = await client.get("/api/finance/export/accountant.csv", headers=tenant)
    assert r.status_code == 200, r.text
    assert "Shop rent Oct" in r.text and "1500" in r.text


@pytest.mark.asyncio
async def test_export_date_range_inclusive(client, tenant):
    from datetime import date
    today = date.today().isoformat()
    await client.post("/api/finance/expenses", json={
        "category": "Utilities", "description": "Power bill", "amount": 320, "expense_date": today}, headers=tenant)
    # date-only range on today must include today's rows (end-of-day inclusive)
    r = await client.get(f"/api/finance/export/accountant.csv?date_from={today}&date_to={today}", headers=tenant)
    assert r.status_code == 200 and "Power bill" in r.text, r.text
    # a past-only window excludes it
    r2 = await client.get("/api/finance/export/accountant.csv?date_from=2000-01-01&date_to=2000-01-31", headers=tenant)
    assert "Power bill" not in r2.text


@pytest.mark.asyncio
async def test_accountant_export_gated(client, tenant):
    # a role with can_export_accountant denied is blocked (deny-wins)
    rid = (await client.post("/api/settings/rbac/roles", json={
        "name": "No Export", "permissions": {"can_export_accountant": False}}, headers=tenant)).json()["id"]
    await client.post("/api/staff/employees", json={
        "name": "NoExp", "email": "noexp-144bk@test.dev", "password": "pw-noexp-12345", "role_id": rid}, headers=tenant)
    tok = (await client.post("/api/auth/token", data={
        "username": "noexp-144bk@test.dev", "password": "pw-noexp-12345"})).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    r = await client.get("/api/finance/export/accountant.csv", headers=h)
    assert r.status_code == 403, r.text
