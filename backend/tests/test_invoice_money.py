"""Invoice money: one set of rules for every payment and invoice path (flow check 30 Oct 2026,
docs/audits/2026-10-30-estimate-to-invoice-flow.md, gaps G4 and F2–F5).

- G4: a deposit lands on the job's invoice, whether the invoice exists yet or not.
- F2: a job payment goes to its invoices by each invoice's balance, never past it.
- F3: one invoice for several jobs credits every job and lists every job's lines.
- F4: a draft invoice follows its job; a sent or paid one keeps what it billed.
- F5: invoice tax and discount are amounts everywhere.
"""
import uuid

import pytest


async def _shop(client):
    r = await client.post("/api/auth/register", json={
        "org_name": "Money", "name": "Owner", "email": f"im-{uuid.uuid4().hex[:8]}@t.dev",
        "password": "pw-test-12345"})
    H = {"Authorization": f"Bearer {r.json()['access_token']}"}
    loc = (await client.get("/api/staff/locations", headers=H)).json()["items"][0]["id"]
    c = (await client.post("/api/clients", json={"name": "Dana", "phone": "+15550101010",
                                                 "email": "dana@example.com"}, headers=H)).json()
    await client.post("/api/cash/accounts", json={"name": "Till", "payment_option": "cash"}, headers=H)
    acc = (await client.get("/api/cash/accounts", headers=H)).json()["items"][0]["id"]
    return H, loc, c["id"], acc


async def _job(client, H, loc, cid, *prices):
    o = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x"},
                           headers=H)).json()
    for i, p in enumerate(prices):
        r = await client.post(f"/api/orders/{o['id']}/lines", json={
            "kind": "custom", "line_type": "labour", "description": f"Work {i + 1}", "qty": 1, "unit_price": p},
            headers=H)
        assert r.status_code in (200, 201), r.text
    return (await client.get(f"/api/orders/{o['id']}", headers=H)).json()


async def _invoices(client, H, oid):
    return (await client.get(f"/api/cash/orders/{oid}/invoices", headers=H)).json()["invoices"]


async def _invoice(client, H, iid):
    r = await client.get(f"/api/finance/invoices/{iid}", headers=H)
    assert r.status_code == 200, r.text
    return r.json()


async def _remaining(client, H, oid):
    return float((await client.get(f"/api/cash/orders/{oid}/payment-context", headers=H)).json()["remaining"])


# ── G4: deposits ──────────────────────────────────────────────────────────────
async def test_deposit_taken_after_the_invoice_lands_on_it(client):
    H, loc, cid, acc = await _shop(client)
    o = await _job(client, H, loc, cid, 200)
    iid = (await client.post("/api/finance/invoices", json={"order_id": o["id"]}, headers=H)).json()["id"]
    r = await client.post(f"/api/cash/orders/{o['id']}/prepay", json={"amount": 50, "account_id": acc}, headers=H)
    assert r.status_code == 201, r.text
    inv = await _invoice(client, H, iid)
    assert float(inv["amount_paid"]) == 50 and inv["status"] == "partial"
    await client.post(f"/api/cash/orders/{o['id']}/pay", json={"method": "cash", "account_id": acc}, headers=H)
    inv = await _invoice(client, H, iid)
    assert inv["status"] == "paid" and float(inv["amount_paid"]) == 200
    assert await _remaining(client, H, o["id"]) == 0


async def test_deposit_larger_than_the_invoice_only_fills_it(client):
    H, loc, cid, acc = await _shop(client)
    o = await _job(client, H, loc, cid, 100)
    await client.post(f"/api/cash/orders/{o['id']}/prepay", json={"amount": 150, "account_id": acc}, headers=H)
    iid = (await client.post("/api/finance/invoices", json={"order_id": o["id"]}, headers=H)).json()["id"]
    inv = await _invoice(client, H, iid)
    assert float(inv["amount_paid"]) == 100 and inv["status"] == "paid"
    # the extra 50 is still the customer's overpayment on the job, refundable as before
    ctx = (await client.get(f"/api/cash/orders/{o['id']}/payment-context", headers=H)).json()
    assert float(ctx["overpaid"]) == 50


# ── F2: route by invoice balance ──────────────────────────────────────────────
async def test_job_payment_fills_each_invoice_only_up_to_its_balance(client):
    H, loc, cid, acc = await _shop(client)
    await client.put("/api/staff/company", json={"auto_draft_invoice_on_close": True}, headers=H)
    o = await _job(client, H, loc, cid, 40, 260)
    small = next(x for x in o["lines"] if float(x["unit_price"]) == 40)
    r = await client.post(f"/api/cash/orders/{o['id']}/invoices", json={"line_ids": [small["id"]]}, headers=H)
    assert r.status_code == 201, r.text
    sub_id = r.json()["invoice_id"]
    r = await client.post(f"/api/cash/orders/{o['id']}/pay", json={"method": "cash", "account_id": acc}, headers=H)
    assert r.status_code == 200, r.text
    sub = await _invoice(client, H, sub_id)
    assert float(sub["amount_paid"]) == 40 and sub["status"] == "paid"
    assert await _remaining(client, H, o["id"]) == 0
    # closing drafts the rest of the job, and the money already taken pays it
    sts = (await client.get("/api/statuses", headers=H)).json()
    closed = next(s["key"] for s in (sts.get("items", sts) if isinstance(sts, dict) else sts)
                  if s.get("status_group") == "closed")
    r = await client.patch(f"/api/orders/{o['id']}/status", json={"status": closed}, headers=H)
    assert r.status_code == 200, r.text
    invs = await _invoices(client, H, o["id"])
    live = [i for i in invs if i["status"] != "cancelled"]
    assert sorted(float(i["total"]) for i in live) == [40, 260]
    assert all(i["status"] == "paid" and float(i["amount_paid"]) == float(i["total"]) for i in live)


async def test_online_payment_for_a_job_is_routed_like_a_till_payment(client):
    H, loc, cid, acc = await _shop(client)
    o = await _job(client, H, loc, cid, 40, 60)
    small = next(x for x in o["lines"] if float(x["unit_price"]) == 40)
    sub_id = (await client.post(f"/api/cash/orders/{o['id']}/invoices", json={"line_ids": [small["id"]]},
                                headers=H)).json()["invoice_id"]
    r = await client.post("/api/payments/checkout", json={"order_id": o["id"], "amount": 100}, headers=H)
    assert r.status_code == 201, r.text
    r = await client.post(f"/api/payments/intents/{r.json()['id']}/simulate-pay")
    assert r.status_code == 200, r.text
    sub = await _invoice(client, H, sub_id)
    assert float(sub["amount_paid"]) == 40 and sub["status"] == "paid"
    assert await _remaining(client, H, o["id"]) == 0


# ── F3: one invoice, several jobs ─────────────────────────────────────────────
async def test_group_invoice_bills_and_credits_every_job(client):
    H, loc, cid, acc = await _shop(client)
    a = await _job(client, H, loc, cid, 100)
    b = await _job(client, H, loc, cid, 50)
    r = await client.post("/api/orders/bulk", json={"order_ids": [a["id"], b["id"]], "action": "group_invoice",
                                                    "value": ""}, headers=H)
    assert r.status_code == 200, r.text
    iid = (await _invoices(client, H, a["id"]))[0]["id"]
    inv = await _invoice(client, H, iid)
    assert {x["id"] for x in inv["orders"]} == {a["id"], b["id"]}
    assert float(inv["total"]) == 150
    assert sum(float(x["total"]) for x in inv["lines"]) == 150      # both jobs' lines, not just A's
    r = await client.post(f"/api/finance/invoices/{iid}/pay", json={"method": "cash", "amount": 150,
                                                                    "account_id": acc}, headers=H)
    assert r.status_code == 200, r.text
    assert await _remaining(client, H, a["id"]) == 0
    assert await _remaining(client, H, b["id"]) == 0               # B can't be paid a second time


async def test_invoice_for_two_jobs_part_payment_goes_job_by_job(client):
    H, loc, cid, acc = await _shop(client)
    a = await _job(client, H, loc, cid, 100)
    b = await _job(client, H, loc, cid, 50)
    iid = (await client.post("/api/finance/invoices", json={"order_ids": [a["id"], b["id"]]},
                             headers=H)).json()["id"]
    await client.post(f"/api/finance/invoices/{iid}/pay", json={"method": "cash", "amount": 120,
                                                                "account_id": acc}, headers=H)
    assert await _remaining(client, H, a["id"]) == 0
    assert await _remaining(client, H, b["id"]) == 30
    inv = await _invoice(client, H, iid)
    assert float(inv["amount_paid"]) == 120 and inv["status"] == "partial"


async def test_job_payment_on_a_shared_invoice_credits_only_that_job(client):
    H, loc, cid, acc = await _shop(client)
    a = await _job(client, H, loc, cid, 100)
    b = await _job(client, H, loc, cid, 50)
    iid = (await client.post("/api/finance/invoices", json={"order_ids": [a["id"], b["id"]]},
                             headers=H)).json()["id"]
    r = await client.post(f"/api/cash/orders/{b['id']}/pay", json={"method": "cash", "account_id": acc}, headers=H)
    assert r.status_code == 200, r.text
    inv = await _invoice(client, H, iid)
    assert float(inv["amount_paid"]) == 50 and inv["status"] == "partial"
    assert await _remaining(client, H, a["id"]) == 100
    assert len(await _invoices(client, H, b["id"])) <= 1            # no second invoice for B


# ── F4: invoice follows the job while it's a draft ────────────────────────────
async def test_draft_invoice_follows_job_line_changes(client):
    H, loc, cid, acc = await _shop(client)
    o = await _job(client, H, loc, cid, 200)
    iid = (await client.post("/api/finance/invoices", json={"order_id": o["id"]}, headers=H)).json()["id"]
    r = await client.post(f"/api/orders/{o['id']}/lines", json={"kind": "custom", "line_type": "part",
                                                                "description": "Part", "qty": 1, "unit_price": 50},
                          headers=H)
    assert r.status_code in (200, 201), r.text
    inv = await _invoice(client, H, iid)
    assert float(inv["subtotal"]) == 250 and float(inv["total"]) == 250
    line = next(x for x in (await client.get(f"/api/orders/{o['id']}", headers=H)).json()["lines"]
                if x["description"] == "Part")
    await client.delete(f"/api/orders/{o['id']}/lines/{line['id']}", headers=H)
    assert float((await _invoice(client, H, iid))["total"]) == 200


async def test_paid_invoice_keeps_what_it_billed(client):
    H, loc, cid, acc = await _shop(client)
    o = await _job(client, H, loc, cid, 200)
    iid = (await client.post("/api/finance/invoices", json={"order_id": o["id"]}, headers=H)).json()["id"]
    await client.post(f"/api/cash/orders/{o['id']}/pay", json={"method": "cash", "account_id": acc}, headers=H)
    first = (await client.get(f"/api/orders/{o['id']}", headers=H)).json()["lines"][0]
    await client.post(f"/api/orders/{o['id']}/lines", json={"kind": "custom", "line_type": "part",
                                                            "description": "Extra part", "qty": 1, "unit_price": 50},
                      headers=H)
    inv = await _invoice(client, H, iid)
    assert inv["status"] == "paid" and float(inv["total"]) == 200
    assert sum(float(x["total"]) for x in inv["lines"]) == 200      # the PDF/lines match its total
    assert await _remaining(client, H, o["id"]) == 50               # the extra is still owed on the job
    # the line it billed can't be repriced under it
    r = await client.patch(f"/api/orders/{o['id']}/lines/{first['id']}", json={"unit_price": 10}, headers=H)
    assert r.status_code == 409, r.text
    assert "invoice" in r.json()["detail"].lower()


async def test_removing_work_below_what_was_paid_holds_the_extra_on_the_job(client):
    H, loc, cid, acc = await _shop(client)
    o = await _job(client, H, loc, cid, 100, 100)
    iid = (await client.post("/api/finance/invoices", json={"order_id": o["id"]}, headers=H)).json()["id"]
    await client.post(f"/api/cash/orders/{o['id']}/pay", json={"method": "cash", "account_id": acc, "amount": 150},
                      headers=H)
    r = await client.delete(f"/api/orders/{o['id']}/lines/{o['lines'][0]['id']}", headers=H)
    assert r.status_code == 200, r.text
    inv = await _invoice(client, H, iid)
    assert float(inv["total"]) == 100 and float(inv["amount_paid"]) == 100 and inv["status"] == "paid"
    ctx = (await client.get(f"/api/cash/orders/{o['id']}/payment-context", headers=H)).json()
    assert float(ctx["overpaid"]) == 50                              # refundable from the job
    r = await client.post(f"/api/cash/orders/{o['id']}/refund", json={"amount": 50, "account_id": acc}, headers=H)
    assert r.status_code == 201, r.text


async def test_a_deposit_on_one_job_never_pays_another_jobs_share(client):
    H, loc, cid, acc = await _shop(client)
    a = await _job(client, H, loc, cid, 100)
    b = await _job(client, H, loc, cid, 50)
    iid = (await client.post("/api/finance/invoices", json={"order_ids": [a["id"], b["id"]]},
                             headers=H)).json()["id"]
    await client.post(f"/api/cash/orders/{b['id']}/prepay", json={"amount": 150, "account_id": acc}, headers=H)
    inv = await _invoice(client, H, iid)
    assert float(inv["amount_paid"]) == 50 and inv["status"] == "partial"
    assert await _remaining(client, H, a["id"]) == 100


async def test_cancelling_a_draft_gives_its_deposit_back_to_the_job(client):
    H, loc, cid, acc = await _shop(client)
    o = await _job(client, H, loc, cid, 100)
    await client.post(f"/api/cash/orders/{o['id']}/prepay", json={"amount": 40, "account_id": acc}, headers=H)
    iid = (await client.post("/api/finance/invoices", json={"order_id": o["id"]}, headers=H)).json()["id"]
    assert float((await _invoice(client, H, iid))["amount_paid"]) == 40
    r = await client.delete(f"/api/finance/invoices/{iid}", headers=H)
    assert r.status_code == 200, r.text
    iid2 = (await client.post("/api/finance/invoices", json={"order_id": o["id"]}, headers=H)).json()["id"]
    assert float((await _invoice(client, H, iid2))["amount_paid"]) == 40
    assert await _remaining(client, H, o["id"]) == 60


async def test_closing_a_job_on_a_shared_invoice_drafts_no_second_invoice(client):
    H, loc, cid, acc = await _shop(client)
    await client.put("/api/staff/company", json={"auto_draft_invoice_on_close": True}, headers=H)
    a = await _job(client, H, loc, cid, 100)
    b = await _job(client, H, loc, cid, 50)
    await client.post("/api/finance/invoices", json={"order_ids": [a["id"], b["id"]]}, headers=H)
    r = await client.patch(f"/api/orders/{b['id']}/status", json={"status": "done"}, headers=H)
    assert r.status_code == 200, r.text
    assert len(await _invoices(client, H, b["id"])) == 1


# ── F5: tax and discount are amounts ──────────────────────────────────────────
@pytest.mark.parametrize("body", [
    {"discount_pct": 10, "tax_pct": 20},
    {"discount": 10, "tax": 20},             # older clients send percentages in discount/tax
])
async def test_manual_invoice_stores_tax_and_discount_as_amounts(client, body):
    H, loc, cid, acc = await _shop(client)
    r = await client.post("/api/finance/invoices", json={
        "client_id": cid, "lines": [{"description": "Setup", "qty": 1, "unit_price": 100}], **body}, headers=H)
    assert r.status_code == 201, r.text
    inv = await _invoice(client, H, r.json()["id"])
    assert float(inv["subtotal"]) == 100
    assert float(inv["discount"]) == 10
    assert float(inv["tax"]) == 18           # 20% of the discounted 90
    assert float(inv["total"]) == 108


# ── attach / detach, auto-draft, send ─────────────────────────────────────────
async def test_attach_and_detach_order_recompute_the_total(client):
    H, loc, cid, acc = await _shop(client)
    a = await _job(client, H, loc, cid, 100)
    b = await _job(client, H, loc, cid, 50)
    iid = (await client.post("/api/finance/invoices", json={"order_id": a["id"]}, headers=H)).json()["id"]
    r = await client.post(f"/api/finance/invoices/{iid}/orders", json={"order_id": b["id"]}, headers=H)
    assert r.status_code == 200, r.text
    assert float(r.json()["total"]) == 150
    await client.delete(f"/api/finance/invoices/{iid}/orders/{b['id']}", headers=H)
    assert float((await _invoice(client, H, iid))["total"]) == 100


async def test_auto_draft_on_close_ignores_a_cancelled_invoice(client):
    H, loc, cid, acc = await _shop(client)
    await client.put("/api/staff/company", json={"auto_draft_invoice_on_close": True}, headers=H)
    o = await _job(client, H, loc, cid, 80)
    iid = (await client.post("/api/finance/invoices", json={"order_id": o["id"]}, headers=H)).json()["id"]
    assert (await client.delete(f"/api/finance/invoices/{iid}", headers=H)).status_code == 200
    r = await client.patch(f"/api/orders/{o['id']}/status", json={"status": "done"}, headers=H)
    assert r.status_code == 200, r.text
    live = [i for i in await _invoices(client, H, o["id"]) if i["status"] != "cancelled"]
    assert len(live) == 1 and float(live[0]["total"]) == 80


async def test_sending_an_invoice_marks_it_issued(client):
    H, loc, cid, acc = await _shop(client)
    o = await _job(client, H, loc, cid, 80)
    iid = (await client.post("/api/finance/invoices", json={"order_id": o["id"]}, headers=H)).json()["id"]
    r = await client.post(f"/api/finance/invoices/{iid}/send", json={}, headers=H)
    assert r.status_code == 200, r.text
    assert (await _invoice(client, H, iid))["status"] == "issued"
    # a sent invoice no longer changes under the customer
    await client.post(f"/api/orders/{o['id']}/lines", json={"kind": "custom", "line_type": "part",
                                                            "description": "More", "qty": 1, "unit_price": 20},
                      headers=H)
    assert float((await _invoice(client, H, iid))["total"]) == 80


# ── payment_received on every path ────────────────────────────────────────────
async def test_payment_received_fires_on_every_payment_path(client, monkeypatch):
    from backend.services import notification_rules as nr
    seen: list[str] = []

    async def _evaluate(db, *, org_id, order_id, event, **kw):
        if event == "payment_received":
            seen.append(order_id)
        return {}
    monkeypatch.setattr(nr, "evaluate", _evaluate)
    H, loc, cid, acc = await _shop(client)

    o1 = await _job(client, H, loc, cid, 100)
    iid = (await client.post("/api/finance/invoices", json={"order_id": o1["id"]}, headers=H)).json()["id"]
    await client.post(f"/api/finance/invoices/{iid}/pay", json={"method": "cash", "amount": 100,
                                                                "account_id": acc}, headers=H)
    assert o1["id"] in seen, "invoice payment"

    o2 = await _job(client, H, loc, cid, 100)
    r = await client.post("/api/payments/checkout", json={"order_id": o2["id"], "amount": 100}, headers=H)
    await client.post(f"/api/payments/intents/{r.json()['id']}/simulate-pay")
    assert o2["id"] in seen, "online payment"

    o3 = await _job(client, H, loc, cid, 100)
    await client.post(f"/api/cash/orders/{o3['id']}/prepay", json={"amount": 30, "account_id": acc}, headers=H)
    assert o3["id"] in seen, "deposit"

    o4 = await _job(client, H, loc, cid, 100)
    r = await client.post(f"/api/cash/orders/{o4['id']}/pay", json={"method": "balance"}, headers=H)
    assert r.status_code == 200, r.text
    assert o4["id"] in seen, "closed on client balance"


def test_migration_turns_old_percentage_invoices_into_amounts():
    """im01: invoices made in the manual editor stored percentages. They become amounts, the total
    unchanged; invoices already in amounts, and ones neither reading fits, are left alone."""
    import importlib.util
    import pathlib

    import sqlalchemy as sa
    path = pathlib.Path(__file__).parents[1] / "migrations" / "versions" / "im01_invoice_sent_at.py"
    spec = importlib.util.spec_from_file_location("im01", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    eng = sa.create_engine("sqlite://")
    with eng.begin() as c:
        c.execute(sa.text("CREATE TABLE invoices (id TEXT, subtotal REAL, discount REAL, tax REAL, total REAL)"))
        c.execute(sa.text("INSERT INTO invoices VALUES ('pct', 100, 10, 20, 108), ('amt', 300, 0, 9, 309),"
                          " ('odd', 100, 5, 5, 77)"))
        mod._percent_to_amounts(c)
        got = {r[0]: r[1:] for r in c.execute(sa.text("SELECT id, discount, tax, total FROM invoices"))}
    assert got["pct"] == (10, 18, 108)
    assert got["amt"] == (0, 9, 309)
    assert got["odd"] == (5, 5, 77)


async def test_invoice_payer_must_be_this_shops_client(client):
    H, loc, cid, acc = await _shop(client)
    H2, _, other_cid, _ = await _shop(client)
    r = await client.post("/api/finance/invoices", json={"client_id": cid, "payer_client_id": other_cid,
                                                         "lines": [{"description": "x", "qty": 1, "unit_price": 5}]},
                          headers=H)
    assert r.status_code == 404, r.text
