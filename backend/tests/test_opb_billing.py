"""Team B (billing): invoice display mode, record payment (partial / overpay) and credit notes.

Ledger convention (services/ledger.py): + the client owes us, − we owe the client.
"""
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import select

pytestmark = pytest.mark.asyncio


# ── helpers ──────────────────────────────────────────────────────────────────
async def _register(client):
    email = f"opb-{uuid.uuid4().hex[:8]}@t.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Billing Co", "name": "Owner", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _teammate(client, H, *deny):
    r = await client.post("/api/settings/rbac/roles", json={
        "name": f"No {deny[0]} {uuid.uuid4().hex[:4]}", "permissions": {c: False for c in deny}}, headers=H)
    assert r.status_code in (200, 201), r.text
    email = f"tm-{uuid.uuid4().hex[:8]}@t.dev"
    r = await client.post("/api/staff/employees", json={
        "name": "Desk", "email": email, "password": "pw-test-12345", "role_id": r.json()["id"]}, headers=H)
    assert r.status_code == 201, r.text
    tok = await client.post("/api/auth/token", data={"username": email, "password": "pw-test-12345"})
    return {"Authorization": f"Bearer {tok.json()['access_token']}"}


async def _client(client, H, name="Ravi"):
    r = await client.post("/api/clients", json={"name": name}, headers=H)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _invoice(client, H, cid, price=100, **extra):
    r = await client.post("/api/finance/invoices", headers=H, json={
        "client_id": cid, "lines": [{"description": "Screen repair", "qty": 1, "unit_price": price}], **extra})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _account(client, H, name="Till"):
    r = await client.post("/api/cash/accounts", json={"name": name, "payment_option": "cash"}, headers=H)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _balance(client, H, cid) -> float:
    return (await client.get(f"/api/clients/{cid}/balance", headers=H)).json()["balance"]


async def _inv(client, H, iid) -> dict:
    r = await client.get(f"/api/finance/invoices/{iid}", headers=H)
    assert r.status_code == 200, r.text
    return r.json()


async def _account_balance(client, H, acc) -> float:
    rows = (await client.get("/api/cash/accounts", headers=H)).json()
    rows = rows.get("items", rows) if isinstance(rows, dict) else rows
    return float(next(a for a in rows if a["id"] == acc)["balance"])


async def _note(client, H, *, cid=None, invoice_id=None, lines=None, reason="Goodwill"):
    r = await client.post("/api/finance/credit-notes", headers=H, json={
        "client_id": cid, "invoice_id": invoice_id, "reason": reason,
        "lines": lines if lines is not None else [{"description": "Refund part", "qty": 1, "unit_price": 30}]})
    assert r.status_code == 201, r.text
    return r.json()


# ── B1 display mode ──────────────────────────────────────────────────────────
async def test_new_invoice_uses_org_default_display_mode(client):
    H = await _register(client)
    r = await client.put("/api/staff/company", json={"default_invoice_display": "one_line"}, headers=H)
    assert r.status_code == 200, r.text
    cid = await _client(client, H)
    assert (await _inv(client, H, await _invoice(client, H, cid)))["display_mode"] == "one_line"
    # an explicit pick wins over the org default
    iid = await _invoice(client, H, cid, display_mode="simplified")
    assert (await _inv(client, H, iid))["display_mode"] == "simplified"
    r = await client.patch(f"/api/finance/invoices/{iid}", json={"display_mode": "detailed"}, headers=H)
    assert r.status_code == 200 and r.json()["display_mode"] == "detailed"
    r = await client.patch(f"/api/finance/invoices/{iid}", json={"display_mode": "fancy"}, headers=H)
    assert r.status_code == 422


# ── B2 record payment ────────────────────────────────────────────────────────
async def test_partial_payment_then_rest(client):
    H = await _register(client)
    cid, acc = await _client(client, H), await _account(client, H)
    iid = await _invoice(client, H, cid, price=100)
    r = await client.post(f"/api/finance/invoices/{iid}/pay", headers=H,
                          json={"amount": 40, "method": "cash", "account_id": acc})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "partial" and r.json()["balance_due"] == 60
    inv = await _inv(client, H, iid)
    assert inv["amount_paid"] == 40 and inv["balance_due"] == 60 and inv["credited"] == 0
    assert await _account_balance(client, H, acc) == 40
    r = await client.post(f"/api/finance/invoices/{iid}/pay", headers=H,
                          json={"amount": 60, "method": "cash", "account_id": acc})
    assert r.json()["status"] == "paid" and r.json()["balance_due"] == 0
    assert await _balance(client, H, cid) == -100     # invoice-only: payments reduce what's owed


async def test_payment_date_and_account_checks(client):
    H = await _register(client)
    cid, acc = await _client(client, H), await _account(client, H)
    iid = await _invoice(client, H, cid)
    future = (date.today() + timedelta(days=2)).isoformat()
    r = await client.post(f"/api/finance/invoices/{iid}/pay", headers=H,
                          json={"amount": 10, "method": "cash", "account_id": acc, "paid_at": future})
    assert r.status_code == 422
    past = (date.today() - timedelta(days=3)).isoformat()
    r = await client.post(f"/api/finance/invoices/{iid}/pay", headers=H,
                          json={"amount": 10, "method": "cash", "account_id": acc, "paid_at": past})
    assert r.status_code == 200, r.text
    pays = (await _inv(client, H, iid))["payments"]
    assert pays[0]["paid_at"].startswith(past)
    T = await _teammate(client, H, "can_retroactive_cash")
    r = await client.post(f"/api/finance/invoices/{iid}/pay", headers=T,
                          json={"amount": 10, "method": "cash", "account_id": acc, "paid_at": past})
    assert r.status_code == 403
    r = await client.post(f"/api/finance/invoices/{iid}/pay", headers=H,
                          json={"amount": 10, "method": "cash", "account_id": str(uuid.uuid4())})
    assert r.status_code == 404


# ── B3 overpayment ───────────────────────────────────────────────────────────
async def test_overpay_needs_a_choice(client):
    H = await _register(client)
    cid = await _client(client, H)
    iid = await _invoice(client, H, cid, price=100)
    r = await client.post(f"/api/finance/invoices/{iid}/pay", headers=H, json={"amount": 150, "method": "cash"})
    assert r.status_code == 400 and "balance" in r.text
    r = await client.post(f"/api/finance/invoices/{iid}/pay", headers=H,
                          json={"amount": 150, "method": "cash", "overpay": "bogus"})
    assert r.status_code == 422


async def test_overpay_kept_as_client_balance(client):
    H = await _register(client)
    cid, acc = await _client(client, H), await _account(client, H)
    iid = await _invoice(client, H, cid, price=100)
    await client.post(f"/api/finance/invoices/{iid}/pay", headers=H,
                      json={"amount": 30, "method": "cash", "account_id": acc})
    r = await client.post(f"/api/finance/invoices/{iid}/pay", headers=H,
                          json={"amount": 120.5, "method": "cash", "account_id": acc, "overpay": "keep_balance"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "paid" and body["applied"] == 70 and body["kept_as_balance"] == 50.5
    inv = await _inv(client, H, iid)
    assert inv["amount_paid"] == 100 and inv["balance_due"] == 0       # never paid beyond its total
    assert await _account_balance(client, H, acc) == 150.5             # all the cash is in the till
    assert await _balance(client, H, cid) == -150.5                   # 50.50 of it is store credit
    led = (await client.get(f"/api/clients/{cid}/balance", headers=H)).json()["entries"]
    assert any(e["entry_type"] == "advance" and e["amount"] == -50.5 for e in led)
    # the invoice is settled now: more money is refused
    r = await client.post(f"/api/finance/invoices/{iid}/pay", headers=H,
                          json={"amount": 5, "method": "cash", "overpay": "keep_balance"})
    assert r.status_code == 409


async def test_overpay_refunded(client, db):
    from backend.models import Payment
    H = await _register(client)
    cid, acc = await _client(client, H), await _account(client, H)
    iid = await _invoice(client, H, cid, price=80)
    r = await client.post(f"/api/finance/invoices/{iid}/pay", headers=H,
                          json={"amount": 100, "method": "cash", "account_id": acc, "overpay": "refund"})
    assert r.status_code == 200, r.text
    assert r.json()["refunded"] == 20 and r.json()["status"] == "paid"
    assert await _account_balance(client, H, acc) == 80               # 100 in, 20 change out
    assert await _balance(client, H, cid) == -80
    kinds = sorted((p.kind, float(p.amount)) for p in (await db.execute(
        select(Payment).where(Payment.invoice_id == iid))).scalars().all())
    assert kinds == [("payment", 100.0), ("refund", 20.0)]


async def test_overpay_rules(client):
    H = await _register(client)
    cid = await _client(client, H)
    walkin = await _invoice(client, H, None, price=50)
    r = await client.post(f"/api/finance/invoices/{walkin}/pay", headers=H,
                          json={"amount": 60, "method": "cash", "overpay": "keep_balance"})
    assert r.status_code == 422                                        # nobody to hold the credit
    iid = await _invoice(client, H, cid, price=50)
    r = await client.post(f"/api/finance/invoices/{iid}/pay", headers=H,
                          json={"amount": 60, "method": "balance", "overpay": "keep_balance"})
    assert r.status_code == 422
    T = await _teammate(client, H, "can_create_returns")
    r = await client.post(f"/api/finance/invoices/{iid}/pay", headers=T,
                          json={"amount": 60, "method": "cash", "overpay": "refund"})
    assert r.status_code == 403


# ── B4 credit notes ──────────────────────────────────────────────────────────
async def test_credit_note_totals_with_tax(client):
    H = await _register(client)
    cid = await _client(client, H)
    cn = await _note(client, H, cid=cid, lines=[
        {"description": "Labour", "qty": 3, "unit_price": 33.33, "tax_rate": 7.5},
        {"description": "Part", "qty": 1, "unit_price": 10.005, "tax_rate": 0}])
    # 3 × 33.33 = 99.99, tax 7.5% = 7.49925 → 7.50; 10.005 → 10.01 (half-up)
    assert cn["status"] == "draft" and cn["number"] is None
    assert cn["subtotal"] == 110.0 and cn["tax_total"] == 7.5 and cn["total"] == 117.5
    assert [ln["total"] for ln in cn["lines"]] == [107.49, 10.01]


async def test_linked_credit_note_reduces_invoice_and_cancel_reverses(client):
    H = await _register(client)
    cid, acc = await _client(client, H), await _account(client, H)
    iid = await _invoice(client, H, cid, price=100)
    await client.post(f"/api/finance/invoices/{iid}/pay", headers=H,
                      json={"amount": 50, "method": "cash", "account_id": acc})
    before = await _balance(client, H, cid)
    cn = await _note(client, H, invoice_id=iid,
                     lines=[{"description": "Discount", "qty": 1, "unit_price": 20, "tax_rate": 0}])
    assert cn["client_id"] == cid and cn["invoice_id"] == iid
    assert (await _inv(client, H, iid))["balance_due"] == 50          # a draft moves nothing
    r = await client.post(f"/api/finance/credit-notes/{cn['id']}/issue", headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "issued" and r.json()["number"] == "CN-00001"
    inv = await _inv(client, H, iid)
    assert inv["credited"] == 20 and inv["balance_due"] == 30 and inv["status"] == "partial"
    assert [c["number"] for c in inv["credit_notes"]] == ["CN-00001"]
    assert await _balance(client, H, cid) == before - 20
    # issued = locked
    r = await client.put(f"/api/finance/credit-notes/{cn['id']}", headers=H,
                         json={"invoice_id": iid, "lines": [{"description": "x", "qty": 1, "unit_price": 1}]})
    assert r.status_code == 409
    assert (await client.delete(f"/api/finance/credit-notes/{cn['id']}", headers=H)).status_code == 409
    assert (await client.post(f"/api/finance/credit-notes/{cn['id']}/issue", headers=H)).status_code == 409
    # cancel reverses both the invoice and the ledger
    r = await client.post(f"/api/finance/credit-notes/{cn['id']}/cancel", headers=H)
    assert r.status_code == 200 and r.json()["status"] == "canceled"
    inv = await _inv(client, H, iid)
    assert inv["credited"] == 0 and inv["balance_due"] == 50 and inv["status"] == "partial"
    assert await _balance(client, H, cid) == before
    assert (await client.post(f"/api/finance/credit-notes/{cn['id']}/cancel", headers=H)).status_code == 409


async def test_credit_note_can_settle_invoice_and_caps_at_total(client):
    H = await _register(client)
    cid = await _client(client, H)
    iid = await _invoice(client, H, cid, price=100)
    full = await _note(client, H, invoice_id=iid, lines=[{"description": "All", "qty": 1, "unit_price": 100}])
    await client.post(f"/api/finance/credit-notes/{full['id']}/issue", headers=H)
    assert (await _inv(client, H, iid))["status"] == "paid"
    more = await _note(client, H, invoice_id=iid, lines=[{"description": "More", "qty": 1, "unit_price": 1}])
    r = await client.post(f"/api/finance/credit-notes/{more['id']}/issue", headers=H)
    assert r.status_code == 422                                        # can't credit beyond the total
    await client.post(f"/api/finance/credit-notes/{full['id']}/cancel", headers=H)
    assert (await _inv(client, H, iid))["status"] == "draft"


async def test_standalone_credit_note_adds_client_credit(client):
    H = await _register(client)
    cid = await _client(client, H)
    cn = await _note(client, H, cid=cid, lines=[{"description": "Goodwill", "qty": 2, "unit_price": 12.5}])
    r = await client.post(f"/api/finance/credit-notes/{cn['id']}/issue", headers=H)
    assert r.status_code == 200
    assert await _balance(client, H, cid) == -25
    led = (await client.get(f"/api/clients/{cid}/balance", headers=H)).json()["entries"]
    assert led[0]["entry_type"] == "credit_note" and led[0]["ref_id"] == cn["id"]
    await client.post(f"/api/finance/credit-notes/{cn['id']}/cancel", headers=H)
    assert await _balance(client, H, cid) == 0
    listed = (await client.get(f"/api/finance/credit-notes?client_id={cid}", headers=H)).json()
    assert listed["total"] == 1 and listed["items"][0]["status"] == "canceled"


async def test_credit_note_validation(client):
    H = await _register(client)
    cid, other = await _client(client, H), await _client(client, H, "Other")
    r = await client.post("/api/finance/credit-notes", headers=H, json={"lines": []})
    assert r.status_code == 422                                        # needs a client or invoice
    iid = await _invoice(client, H, cid)
    r = await client.post("/api/finance/credit-notes", headers=H, json={"invoice_id": iid, "client_id": other})
    assert r.status_code == 422                                        # client must match the invoice
    r = await client.post("/api/finance/credit-notes", headers=H,
                          json={"client_id": cid, "lines": [{"description": " ", "qty": 1, "unit_price": 1}]})
    assert r.status_code == 422
    empty = await _note(client, H, cid=cid, lines=[])
    assert (await client.post(f"/api/finance/credit-notes/{empty['id']}/issue", headers=H)).status_code == 422
    await client.delete(f"/api/finance/invoices/{iid}", headers=H)
    r = await client.post("/api/finance/credit-notes", headers=H, json={"invoice_id": iid})
    assert r.status_code == 409                                        # cancelled invoice


async def test_numbering_is_sequential_and_skips_drafts(client):
    H = await _register(client)
    cid = await _client(client, H)
    a = await _note(client, H, cid=cid)
    b = await _note(client, H, cid=cid)
    c = await _note(client, H, cid=cid)
    assert (await client.delete(f"/api/finance/credit-notes/{a['id']}", headers=H)).status_code == 200
    n1 = (await client.post(f"/api/finance/credit-notes/{c['id']}/issue", headers=H)).json()["number"]
    n2 = (await client.post(f"/api/finance/credit-notes/{b['id']}/issue", headers=H)).json()["number"]
    assert (n1, n2) == ("CN-00001", "CN-00002")                        # deleted draft left no gap
    nums = (await client.get("/api/settings/numbering", headers=H)).json()
    rows = nums.get("items", nums) if isinstance(nums, dict) else nums
    assert any(r["doc_type"] == "credit_note" for r in rows)


async def test_order_balance_counts_credit_notes(client, db):
    H = await _register(client)
    cid = await _client(client, H)
    r = await client.post("/api/orders", json={"client_id": cid, "complaint": "x", "lines": [
        {"line_type": "labour", "kind": "custom", "description": "Fix", "qty": 1, "unit_price": 100}]}, headers=H)
    oid = r.json()["id"]
    iid = (await client.post("/api/finance/invoices", headers=H, json={"order_id": oid, "client_id": cid})).json()["id"]
    cn = await _note(client, H, invoice_id=iid, lines=[{"description": "Off", "qty": 1, "unit_price": 25}])
    await client.post(f"/api/finance/credit-notes/{cn['id']}/issue", headers=H)
    from backend.models import Order
    from backend.services.order_money import balance_due
    order = (await db.execute(select(Order).where(Order.id == oid))).scalar_one()
    assert await balance_due(db, order) == float(order.total) - 25


async def test_print_pdf_and_template(client):
    H = await _register(client)
    cid = await _client(client, H)
    cn = await _note(client, H, cid=cid)
    r = await client.get(f"/api/print/credit-notes/{cn['id']}", headers=H)
    assert r.status_code == 200 and r.content[:4] == b"%PDF"
    tpls = (await client.get("/api/documents/templates", headers=H)).json()["items"]
    tpl = next(t for t in tpls if t["doc_type"] == "credit_note")
    r = await client.get(f"/api/documents/render-credit-note/{cn['id']}/{tpl['id']}", headers=H)
    assert r.status_code == 200, r.text
    assert "Draft" in r.json()["html"] and "Refund part" in r.json()["html"]
    r = await client.post("/api/documents/render-credit-note-batch", headers=H,
                          json={"template_id": tpl["id"], "credit_note_ids": [cn["id"]]})
    assert r.status_code == 200 and r.json()["count"] == 1


async def test_v1_read_endpoints(client):
    H = await _register(client)
    cid = await _client(client, H)
    cn = await _note(client, H, cid=cid)
    r = await client.post("/api/api-keys", json={"name": "zap", "scopes": "*"}, headers=H)
    key = r.json().get("key") or r.json().get("token") or r.json().get("plaintext")
    K = {"Authorization": f"Bearer {key}"}
    r = await client.get("/api/v1/credit-notes", headers=K)
    assert r.status_code == 200 and r.json()["data"][0]["id"] == cn["id"]
    r = await client.get(f"/api/v1/credit-notes/{cn['id']}", headers=K)
    assert r.status_code == 200 and r.json()["data"]["lines"][0]["total"] == 30
    assert (await client.get(f"/api/v1/credit-notes/{uuid.uuid4()}", headers=K)).status_code == 404
    iid = await _invoice(client, H, cid)
    inv = (await client.get(f"/api/v1/invoices/{iid}", headers=K)).json()["data"]
    assert inv["credited"] == 0 and inv["balance_due"] == 100


# ── permissions (403) and tenant isolation (404) ─────────────────────────────
async def test_permissions_403(client):
    H = await _register(client)
    cid = await _client(client, H)
    cn = await _note(client, H, cid=cid)
    base = "/api/finance/credit-notes"
    body = {"client_id": cid, "lines": [{"description": "x", "qty": 1, "unit_price": 1}]}
    V = await _teammate(client, H, "can_view_credit_notes")
    for path in (base, f"{base}/{cn['id']}", f"/api/print/credit-notes/{cn['id']}"):
        assert (await client.get(path, headers=V)).status_code == 403, path
    C = await _teammate(client, H, "can_create_credit_notes")
    assert (await client.post(base, json=body, headers=C)).status_code == 403
    assert (await client.put(f"{base}/{cn['id']}", json=body, headers=C)).status_code == 403
    assert (await client.delete(f"{base}/{cn['id']}", headers=C)).status_code == 403
    Iss = await _teammate(client, H, "can_issue_credit_notes")
    assert (await client.post(f"{base}/{cn['id']}/issue", headers=Iss)).status_code == 403
    assert (await client.post(f"{base}/{cn['id']}/cancel", headers=Iss)).status_code == 403
    F = await _teammate(client, H, "can_view_finance")
    iid = await _invoice(client, H, cid)
    assert (await client.post(f"/api/finance/invoices/{iid}/pay", json={"amount": 1, "method": "cash"},
                              headers=F)).status_code == 403


async def test_other_org_gets_404(client):
    A, B = await _register(client), await _register(client)
    cid = await _client(client, A)
    iid = await _invoice(client, A, cid)
    cn = await _note(client, A, cid=cid)
    base = "/api/finance/credit-notes"
    body = {"lines": [{"description": "x", "qty": 1, "unit_price": 1}]}
    assert (await client.get(f"{base}/{cn['id']}", headers=B)).status_code == 404
    assert (await client.put(f"{base}/{cn['id']}", json={**body, "client_id": None, "invoice_id": None},
                             headers=B)).status_code == 404
    assert (await client.delete(f"{base}/{cn['id']}", headers=B)).status_code == 404
    assert (await client.post(f"{base}/{cn['id']}/issue", headers=B)).status_code == 404
    assert (await client.post(f"{base}/{cn['id']}/cancel", headers=B)).status_code == 404
    assert (await client.get(f"/api/print/credit-notes/{cn['id']}", headers=B)).status_code == 404
    # B can't point a note at A's client or invoice either
    assert (await client.post(base, json={**body, "client_id": cid}, headers=B)).status_code == 404
    assert (await client.post(base, json={**body, "invoice_id": iid}, headers=B)).status_code == 404
    assert (await client.get(base, headers=B)).json()["total"] == 0
    assert (await client.post(f"/api/finance/invoices/{iid}/pay", json={"amount": 1, "method": "cash"},
                              headers=B)).status_code == 404
    tpls = (await client.get("/api/documents/templates", headers=B)).json()["items"]
    tpl = next(t for t in tpls if t["doc_type"] == "credit_note")
    assert (await client.get(f"/api/documents/render-credit-note/{cn['id']}/{tpl['id']}",
                             headers=B)).status_code == 404


async def test_migration_grants_credit_note_codes_to_explicit_roles():
    """The data step in opb1: explicit roles get the credit-note codes matching their invoice codes."""
    import importlib.util
    from pathlib import Path
    p = Path(__file__).resolve().parents[1] / "migrations" / "versions" / "opb1_credit_notes.py"
    spec = importlib.util.spec_from_file_location("opb1", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.revision.startswith("opb")   # down_revision is re-chained at integration
    assert "can_edit_issued_invoices" in mod._GRANTS["can_issue_credit_notes"]


async def test_invoice_with_issued_credit_cant_be_cancelled(client):
    H = await _register(client)
    cid = await _client(client, H)
    iid = await _invoice(client, H, cid)
    cn = await _note(client, H, invoice_id=iid)
    await client.post(f"/api/finance/credit-notes/{cn['id']}/issue", headers=H)
    assert (await client.delete(f"/api/finance/invoices/{iid}", headers=H)).status_code == 409
    await client.post(f"/api/finance/credit-notes/{cn['id']}/cancel", headers=H)
    assert (await client.delete(f"/api/finance/invoices/{iid}", headers=H)).status_code == 200


async def test_multi_order_invoice_shares_credit(client, db):
    from backend.models import Order
    from backend.services.order_money import credited
    H = await _register(client)
    cid = await _client(client, H)
    oids = []
    for price in (100, 300):
        r = await client.post("/api/orders", json={"client_id": cid, "complaint": "x", "lines": [
            {"line_type": "labour", "kind": "custom", "description": "Fix", "qty": 1, "unit_price": price}]}, headers=H)
        oids.append(r.json()["id"])
    iid = (await client.post("/api/finance/invoices", headers=H, json={"order_ids": oids, "client_id": cid})).json()["id"]
    cn = await _note(client, H, invoice_id=iid, lines=[{"description": "Off", "qty": 1, "unit_price": 40}])
    await client.post(f"/api/finance/credit-notes/{cn['id']}/issue", headers=H)
    totals = {o.id: float(o.total) for o in (await db.execute(select(Order).where(Order.id.in_(oids)))).scalars()}
    shares = [await credited(db, oid) for oid in oids]
    whole = sum(totals.values())
    assert shares == [round(40 * totals[oids[0]] / whole, 2), round(40 * totals[oids[1]] / whole, 2)]
