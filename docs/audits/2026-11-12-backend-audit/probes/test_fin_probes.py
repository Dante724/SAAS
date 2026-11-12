"""FIN audit probes (read-only audit; lives in the scratchpad, not the tree)."""
import uuid


async def _acct(client, h, name="Till", opt="cash"):
    await client.post("/api/cash/accounts", json={"name": name, "payment_option": opt}, headers=h)
    return [x for x in (await client.get("/api/cash/accounts", headers=h)).json()["items"] if x["name"] == name][0]["id"]


async def _bal(client, h, aid):
    return [x for x in (await client.get("/api/cash/accounts", headers=h)).json()["items"] if x["id"] == aid][0]["balance"] or 0.0


async def _client(client, h, name="Probe Client"):
    r = await client.post("/api/clients", json={"name": name, "phone": "+1555" + uuid.uuid4().hex[:7]}, headers=h)
    return r.json()["id"]


async def _order(client, h, cid, price=100):
    r = await client.post("/api/orders", headers=h, json={
        "client_id": cid, "complaint": "job",
        "lines": [{"line_type": "labour", "description": "Repair", "qty": 1, "unit_price": price}]})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def _mismatches(org_checks=("account", "client", "invoice")):
    from backend.tests.conftest import TestSession
    from backend.services.reconcile import find_mismatches
    async with TestSession() as s:
        return await find_mismatches(s, checks=org_checks)


# FIN probe 1: the same refund sent twice (double click / network retry with the SAME
# Idempotency-Key) is booked twice — /api/returns/refunds has no idempotency or double-click guard.
async def test_probe_duplicate_refund(client, tenant):
    h = tenant
    aid = await _acct(client, h)
    cid = await _client(client, h)
    oid = await _order(client, h, cid)
    r = await client.post(f"/api/cash/orders/{oid}/pay", json={"method": "cash", "account_id": aid, "amount": 100},
                          headers=h)
    assert r.status_code == 200, r.text
    hk = {**h, "Idempotency-Key": "refund-key-1"}
    body = {"account_id": aid, "amount": 30, "order_id": oid, "reason": "goodwill"}
    r1 = await client.post("/api/returns/refunds", json=body, headers=hk)
    r2 = await client.post("/api/returns/refunds", json=body, headers=hk)
    print("refund1", r1.status_code, r1.text[:120])
    print("refund2", r2.status_code, r2.text[:120])
    print("till after", await _bal(client, h, aid))
    assert r1.status_code == 201 and r2.status_code == 201
    assert r1.json()["id"] != r2.json()["id"]
    assert await _bal(client, h, aid) == 40.0      # 100 in, 30 + 30 out


# FIN probe 1b: same for the deposit/overpayment refund in /api/cash/orders/{id}/refund
async def test_probe_duplicate_deposit_refund(client, tenant):
    h = tenant
    aid = await _acct(client, h)
    cid = await _client(client, h)
    oid = await _order(client, h, cid)
    r = await client.post(f"/api/cash/orders/{oid}/prepay", json={"amount": 80, "account_id": aid}, headers=h)
    assert r.status_code == 201, r.text
    hk = {**h, "Idempotency-Key": "dep-refund-1"}
    body = {"amount": 40, "account_id": aid}
    r1 = await client.post(f"/api/cash/orders/{oid}/refund", json=body, headers=hk)
    r2 = await client.post(f"/api/cash/orders/{oid}/refund", json=body, headers=hk)
    print("dep refund", r1.status_code, r2.status_code, r2.text[:120], "till", await _bal(client, h, aid))
    assert r1.status_code == 201 and r2.status_code == 201
    assert await _bal(client, h, aid) == 0.0


# FIN probe 2: deleting the cash leg of a client advance (ref_type "client") reverses the till but
# leaves the client's credit; the credit then pays a job. No reconcile check catches it.
async def test_probe_delete_client_advance_leg(client, tenant):
    h = tenant
    aid = await _acct(client, h)
    cid = await _client(client, h)
    r = await client.post(f"/api/clients/{cid}/balance", json={"kind": "advance", "amount": 100, "account_id": aid},
                          headers=h)
    assert r.status_code == 201, r.text
    txns = (await client.get("/api/cash/transactions", headers=h, params={"account_id": aid})).json()
    items = txns.get("items", txns if isinstance(txns, list) else [])
    t = [x for x in items if (x.get("ref_type") == "client")][0]
    d = await client.delete(f"/api/cash/transactions/{t['id']}", headers=h)
    print("delete client leg", d.status_code, d.text[:150])
    assert d.status_code == 200
    assert await _bal(client, h, aid) == 0.0
    bal = (await client.get(f"/api/clients/{cid}/balance", headers=h)).json()
    print("client balance after delete", bal if not isinstance(bal, dict) else {k: bal[k] for k in list(bal)[:4]})
    oid = await _order(client, h, cid)
    p = await client.post(f"/api/cash/orders/{oid}/pay", json={"method": "balance", "amount": 100}, headers=h)
    print("pay from credit", p.status_code, p.text[:150])
    assert p.status_code == 200
    print("mismatches", await _mismatches())


# FIN probe 3: deleting the supplier payment's cash leg reverses the till, Supplier.balance stays paid.
async def test_probe_delete_supplier_payment_leg(client, tenant):
    h = tenant
    aid = await _acct(client, h)
    await client.post("/api/cash/transactions", json={"account_id": aid, "txn_type": "income", "amount": 500}, headers=h)
    s = await client.post("/api/suppliers", json={"name": "Parts Ltd"}, headers=h)
    assert s.status_code in (200, 201), s.text
    sid = s.json()["id"]
    p = await client.post(f"/api/suppliers/{sid}/pay", json={"amount": 120, "account_id": aid}, headers=h)
    assert p.status_code == 200, p.text
    after_pay = p.json()["balance"]
    txns = (await client.get("/api/cash/transactions", headers=h, params={"account_id": aid})).json()
    items = txns.get("items", [])
    t = [x for x in items if x.get("ref_type") == "supplier_payment"][0]
    d = await client.delete(f"/api/cash/transactions/{t['id']}", headers=h)
    print("delete supplier leg", d.status_code, "till", await _bal(client, h, aid))
    assert d.status_code == 200
    sup = (await client.get(f"/api/suppliers/{sid}", headers=h)).json()
    print("supplier balance after pay", after_pay, "after delete", sup.get("balance"))


# FIN probe 4: removing a work order's gift-card discount line does not give the card its money back.
async def test_probe_giftcard_line_delete(client, tenant):
    h = tenant
    cid = await _client(client, h)
    code = (await client.post("/api/giftcards", json={"value": 60, "client_id": cid}, headers=h)).json()["code"]
    oid = await _order(client, h, cid)
    r = await client.post(f"/api/orders/{oid}/redeem-giftcard", json={"code": code, "amount": 60}, headers=h)
    assert r.status_code == 200, r.text
    o = (await client.get(f"/api/orders/{oid}", headers=h)).json()
    line = [l for l in o["lines"] if (l.get("description") or "").startswith("Gift card")][0]
    d = await client.delete(f"/api/orders/{oid}/lines/{line['id']}", headers=h)
    print("delete gift line", d.status_code, d.text[:120])
    card = (await client.get(f"/api/giftcards/{code}", headers=h)).json()
    o2 = (await client.get(f"/api/orders/{oid}", headers=h)).json()
    print("card balance", card["balance"], "order total", o2.get("total"))
    assert d.status_code == 200 and float(card["balance"]) == 0.0 and float(o2["total"]) == 100.0


# FIN probe 5: an invoice payment can be booked onto a CLOSED drawer shift; and a "cash" invoice
# payment without an account books no cash transaction.
async def test_probe_closed_shift_and_cash_without_account(client, tenant):
    h = tenant
    aid = await _acct(client, h)
    me = (await client.get("/api/auth/me", headers=h)).json()
    locs = (await client.get("/api/locations", headers=h))
    loc_id = me.get("location_id") or (locs.json()[0]["id"] if locs.status_code == 200 and isinstance(locs.json(), list) else None)
    if not loc_id:
        from backend.tests.conftest import TestSession
        from sqlalchemy import select
        from backend.models import Location
        async with TestSession() as s:
            loc_id = (await s.execute(select(Location.id).where(Location.org_id == me["org_id"]))).scalars().first()
    sh = await client.post("/api/finance/shifts/open", json={"location_id": loc_id, "opening_float": -50}, headers=h)
    print("open with negative float", sh.status_code, sh.text[:100])
    shift_id = sh.json()["id"]
    c = await client.post(f"/api/finance/shifts/{shift_id}/close", json={"closing_float": -50}, headers=h)
    assert c.status_code == 200, c.text
    cid = await _client(client, h)
    oid = await _order(client, h, cid)
    inv = await client.post("/api/finance/invoices", json={"order_id": oid}, headers=h)
    iid = inv.json()["id"]
    p = await client.post(f"/api/finance/invoices/{iid}/pay", json={"method": "cash", "amount": 40, "account_id": aid,
                                                                    "shift_id": shift_id}, headers=h)
    print("pay onto closed shift", p.status_code, p.text[:120])
    p2 = await client.post(f"/api/finance/invoices/{iid}/pay", json={"method": "cash", "amount": 60}, headers=h)
    print("cash pay without account", p2.status_code, p2.text[:160], "till", await _bal(client, h, aid))
    from backend.tests.conftest import TestSession
    from sqlalchemy import select
    from backend.models import Payment, Transaction
    async with TestSession() as s:
        pays = (await s.execute(select(Payment.amount, Payment.shift_id, Payment.account_id).where(Payment.invoice_id == iid))).all()
        txns = (await s.execute(select(Transaction.amount).where(Transaction.ref_id == iid))).all()
    print("payments", pays, "txns", txns)
    e = await client.post("/api/finance/expenses", json={"description": "neg", "amount": -1000}, headers=h)
    print("negative expense", e.status_code)
