"""Build 124 — the unified contact settlement ledger.

Every mutual movement of goods, services and cash auto-posts one signed row against a
contact, and the running balance is their sum. Convention: +amount = the contact owes
us (A/R); -amount = we owe the contact (credit / A/P). A supplier is a contact, so its
postings land in the same ledger.
"""
import pytest


# ── helpers ────────────────────────────────────────────────
async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


async def _client(client, h, name="Ledger Client"):
    r = await client.post("/api/clients", json={"name": name, "phone": "+15551000"}, headers=h)
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def _order_with_total(client, h, cid, price=1000):
    """Create a work order for a client, add one labour line, return (order_id, total)."""
    o = (await client.post("/api/orders", json={
        "location_id": await _loc(client, h), "complaint": "screen", "client_id": cid,
    }, headers=h)).json()
    oid = o["id"]
    await client.post(f"/api/orders/{oid}/lines", json={
        "line_type": "labour", "kind": "custom", "description": "Repair",
        "qty": 1, "unit_price": price}, headers=h)
    total = (await client.get(f"/api/orders/{oid}", headers=h)).json()["total"]
    return oid, float(total)


async def _cash(client, h):
    """A cash drawer to take payments into ("pay from balance" needs credit the client holds, B3)."""
    r = await client.post("/api/cash/accounts", json={"name": "Cash", "payment_option": "cash"}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _balance(client, h, cid):
    return (await client.get(f"/api/clients/{cid}/balance", headers=h)).json()


# ── charge on close ────────────────────────────────────────
@pytest.mark.asyncio
async def test_closing_order_charges_client(client, tenant):
    cid = await _client(client, tenant)
    oid, total = await _order_with_total(client, tenant, cid)
    assert total > 0

    r = await client.patch(f"/api/orders/{oid}/status", json={"status": "done"}, headers=tenant)
    assert r.status_code == 200, r.text

    bal = await _balance(client, tenant, cid)
    assert bal["balance"] == pytest.approx(total)          # client now owes us the total
    charges = [e for e in bal["entries"] if e["entry_type"] == "charge"]
    assert len(charges) == 1
    assert charges[0]["amount"] == pytest.approx(total)
    assert charges[0]["ref_type"] == "order" and charges[0]["ref_id"] == oid


# ── payment posts income and nets the balance (the sign fix) ─
@pytest.mark.asyncio
async def test_payment_nets_balance_to_zero(client, tenant):
    cid = await _client(client, tenant)
    oid, total = await _order_with_total(client, tenant, cid)
    await client.patch(f"/api/orders/{oid}/status", json={"status": "done"}, headers=tenant)

    # a payment must REDUCE the receivable, not double it
    r = await client.post(f"/api/cash/orders/{oid}/pay",
                          json={"method": "cash", "account_id": await _cash(client, tenant), "amount": total}, headers=tenant)
    assert r.status_code == 200, r.text

    bal = await _balance(client, tenant, cid)
    assert bal["balance"] == pytest.approx(0.0)            # charge (+T) + income (-T) = 0
    incomes = [e for e in bal["entries"] if e["entry_type"] == "income"]
    assert len(incomes) == 1 and incomes[0]["amount"] == pytest.approx(-total)


# ── reopen reverses the charge ─────────────────────────────
@pytest.mark.asyncio
async def test_reopen_reverses_charge(client, tenant):
    cid = await _client(client, tenant)
    oid, total = await _order_with_total(client, tenant, cid)
    await client.patch(f"/api/orders/{oid}/status", json={"status": "done"}, headers=tenant)
    assert (await _balance(client, tenant, cid))["balance"] == pytest.approx(total)

    r = await client.patch(f"/api/orders/{oid}/status", json={"status": "in_repair"}, headers=tenant)
    assert r.status_code == 200, r.text
    assert (await _balance(client, tenant, cid))["balance"] == pytest.approx(0.0)


# ── re-closing does not double-bill ────────────────────────
@pytest.mark.asyncio
async def test_close_reopen_close_is_idempotent(client, tenant):
    cid = await _client(client, tenant)
    oid, total = await _order_with_total(client, tenant, cid)
    for st in ("done", "in_repair", "done"):
        await client.patch(f"/api/orders/{oid}/status", json={"status": st}, headers=tenant)
    assert (await _balance(client, tenant, cid))["balance"] == pytest.approx(total)


# ── payments tab ───────────────────────────────────────────
@pytest.mark.asyncio
async def test_payments_tab_lists_payment(client, tenant):
    cid = await _client(client, tenant)
    oid, total = await _order_with_total(client, tenant, cid)
    await client.patch(f"/api/orders/{oid}/status", json={"status": "done"}, headers=tenant)
    await client.post(f"/api/cash/orders/{oid}/pay",
                      json={"method": "cash", "account_id": await _cash(client, tenant), "amount": total}, headers=tenant)

    pays = (await client.get(f"/api/clients/{cid}/payments", headers=tenant)).json()["items"]
    assert len(pays) == 1
    assert pays[0]["amount"] == pytest.approx(total)
    assert pays[0]["order_id"] == oid and pays[0]["order_number"]


# ── supplier posting lands in the same unified ledger ──────
@pytest.mark.asyncio
async def test_supplier_posting_hits_unified_ledger(client, tenant):
    sup = (await client.post("/api/suppliers", json={
        "name": "Acme Parts", "phone": "+15552000"}, headers=tenant)).json()
    sid = sup["id"]
    loc = await _loc(client, tenant)
    pid = (await client.post("/api/inventory/parts",
                             json={"name": "Battery", "sku": "BAT-1"}, headers=tenant)).json()["id"]

    r = await client.post("/api/inventory/postings", json={
        "location_id": loc, "supplier_id": sid,
        "lines": [{"part_id": pid, "qty": 2, "purchase_price": 250}]}, headers=tenant)
    assert r.status_code == 201, r.text
    cost = r.json()["total_cost"]                          # 500

    led = (await client.get(f"/api/suppliers/{sid}/ledger", headers=tenant)).json()
    assert led["supplier_balance"] == pytest.approx(cost)   # we owe them (supplier convention +)
    postings = [e for e in led["entries"] if e["entry_type"] == "posting"]
    assert len(postings) == 1
    assert postings[0]["amount"] == pytest.approx(-cost)    # unified ledger: we owe → negative
    # and the contact's own balance reflects the same debt
    assert led["contact_balance"] == pytest.approx(-cost)


# ── reporting: A/R reflects the receivable ─────────────────
@pytest.mark.asyncio
async def test_dashboard_ar_reflects_open_receivable(client, tenant):
    cid = await _client(client, tenant, name="AR Client")
    oid, total = await _order_with_total(client, tenant, cid)
    await client.patch(f"/api/orders/{oid}/status", json={"status": "done"}, headers=tenant)

    kpis = (await client.get("/api/reports/company-dashboard", headers=tenant)).json()["kpis"]
    assert "ar" in kpis and "ap" in kpis
    assert kpis["ar"] >= total - 0.001                     # client owes us at least this order


# ── manual advance/payout still works (build 116 regression) ─
@pytest.mark.asyncio
async def test_manual_advance_still_posts(client, tenant):
    cid = await _client(client, tenant)
    r = await client.post(f"/api/clients/{cid}/balance",
                          json={"kind": "advance", "amount": 300}, headers=tenant)
    assert r.status_code == 201 and r.json()["balance"] == pytest.approx(-300.0)


# ── build 125: /auth/me exposes the org currency ───────────
@pytest.mark.asyncio
async def test_me_exposes_currency(client, tenant):
    await client.put("/api/staff/company", json={"name": "Cur Co", "currency": "$",
                     "currency_code": "USD"}, headers=tenant)
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    assert me["currency"] == "$" and me["currency_code"] == "USD"


# ── build 125: historical rebuild is idempotent ────────────
@pytest.mark.asyncio
async def test_rebuild_is_idempotent(client, tenant):
    cid = await _client(client, tenant)
    oid, total = await _order_with_total(client, tenant, cid)
    await client.patch(f"/api/orders/{oid}/status", json={"status": "done"}, headers=tenant)
    await client.post(f"/api/cash/orders/{oid}/pay",
                      json={"method": "cash", "account_id": await _cash(client, tenant), "amount": total}, headers=tenant)
    before = await _balance(client, tenant, cid)
    assert before["balance"] == pytest.approx(0.0)
    n_before = len(before["entries"])

    for _ in range(2):
        r = await client.post("/api/staff/ledger/rebuild", json={}, headers=tenant)
        assert r.status_code == 200, r.text

    after = await _balance(client, tenant, cid)
    assert after["balance"] == pytest.approx(0.0)          # unchanged
    assert len(after["entries"]) == n_before               # no duplicate rows


# ── build 125: rebuild reconstructs a wiped ledger ─────────
@pytest.mark.asyncio
async def test_rebuild_reconstructs_history(client, tenant, db):
    from sqlalchemy import delete, select

    from backend.models import Client, ClientLedgerEntry
    cid = await _client(client, tenant)
    oid, total = await _order_with_total(client, tenant, cid)
    await client.patch(f"/api/orders/{oid}/status", json={"status": "done"}, headers=tenant)
    await client.post(f"/api/cash/orders/{oid}/pay",
                      json={"method": "cash", "account_id": await _cash(client, tenant), "amount": total}, headers=tenant)

    # simulate legacy data: wipe the auto-posted ledger and leave a stale balance
    await db.execute(delete(ClientLedgerEntry).where(ClientLedgerEntry.client_id == cid))
    c = (await db.execute(select(Client).where(Client.id == cid))).scalar_one()
    c.balance = 12345.0
    await db.commit()

    r = await client.post("/api/staff/ledger/rebuild", json={}, headers=tenant)
    assert r.status_code == 200, r.text
    assert r.json()["orders_charged"] == 1 and r.json()["payments_posted"] == 1

    bal = await _balance(client, tenant, cid)
    assert bal["balance"] == pytest.approx(0.0)             # charge (+T) + income (-T)
    types = sorted(e["entry_type"] for e in bal["entries"])
    assert types == ["charge", "income"]
