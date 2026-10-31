"""The rest of the 30 Oct 2026 flow check (docs/audits/2026-10-30-estimate-to-invoice-flow.md) that the
comms (#59) and invoice-money (#60) fixes don't cover: E5 estimate extras, J4 tasks on the job, J6 small
job items (type/priority, timers on close, QuickBooks lines), the Xero push, and customer approvals of
recommended work going through the normal status change.
"""
import uuid
from datetime import date, timedelta

from sqlalchemy import insert, select

from backend.models import Task, TimeEntry

PNG = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGA"
       "hKmMIQAAAABJRU5ErkJggg==")


async def _shop(client, name="Extras"):
    r = await client.post("/api/auth/register", json={"org_name": name, "name": "Owner",
                                                      "email": f"extras-{uuid.uuid4().hex[:8]}@t.dev",
                                                      "password": "pw-test-12345"})
    H = {"Authorization": f"Bearer {r.json()['access_token']}"}
    me = (await client.get("/api/auth/me", headers=H)).json()
    loc = (await client.get("/api/staff/locations", headers=H)).json()["items"][0]["id"]
    c = (await client.post("/api/clients", json={"name": "Dana", "phone": "+15550101010",
                                                 "email": "dana@example.com"}, headers=H)).json()
    return H, me, loc, c["id"]


async def _line(client, H, oid, desc, price):
    r = await client.post(f"/api/orders/{oid}/lines", json={"kind": "custom", "line_type": "labour",
                                                            "description": desc, "qty": 1, "unit_price": price},
                          headers=H)
    assert r.status_code == 201, r.text
    return r.json()


async def _estimate(client, H, loc, cid, prices=(50,)):
    o = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x",
                                                "is_estimate": True}, headers=H)).json()
    for i, p in enumerate(prices):
        await _line(client, H, o["id"], f"Work {i + 1}", p)
    tok = (await client.post(f"/api/orders/{o['id']}/public-link", headers=H)).json()["token"]
    return o, tok


async def _statuses(client, H):
    sts = (await client.get("/api/statuses", headers=H)).json()
    return sts.get("items", sts) if isinstance(sts, dict) else sts


async def _track(client, tok):
    return (await client.get(f"/api/public/track/{tok}")).json()


# ── E5: valid until ──
async def test_estimate_valid_until_is_shown_and_an_expired_one_is_refused(client):
    H, _, loc, cid = await _shop(client)
    o, tok = await _estimate(client, H, loc, cid)
    soon = (date.today() + timedelta(days=14)).isoformat()
    r = await client.patch(f"/api/orders/{o['id']}/estimate", json={"valid_until": soon}, headers=H)
    assert r.status_code == 200, r.text
    assert (await client.get(f"/api/orders/{o['id']}", headers=H)).json()["estimate_valid_until"] == soon
    page = await _track(client, tok)
    assert page["valid_until"] == soon and page["expired"] is False and page["awaiting_approval"] is True

    past = (date.today() - timedelta(days=1)).isoformat()
    await client.patch(f"/api/orders/{o['id']}/estimate", json={"valid_until": past}, headers=H)
    page = await _track(client, tok)
    assert page["expired"] is True and page["awaiting_approval"] is False
    r = await client.post(f"/api/public/track/{tok}/respond", json={"decision": "accept", "signature": PNG})
    assert r.status_code == 410, r.text
    assert "expired" in r.json()["detail"].lower()
    # clearing the date opens it again
    await client.patch(f"/api/orders/{o['id']}/estimate", json={"valid_until": None}, headers=H)
    assert (await _track(client, tok))["awaiting_approval"] is True


async def test_valid_until_only_on_estimates(client):
    H, _, loc, cid = await _shop(client)
    o = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x"}, headers=H)).json()
    r = await client.patch(f"/api/orders/{o['id']}/estimate", json={"valid_until": "2030-01-01"}, headers=H)
    assert r.status_code == 409


# ── E5: decline reason ──
async def test_decline_reason_reaches_staff(client):
    H, _, loc, cid = await _shop(client)
    o, tok = await _estimate(client, H, loc, cid)
    r = await client.post(f"/api/public/track/{tok}/respond",
                          json={"decision": "decline", "name": "Dana", "reason": "Too expensive for now"})
    assert r.status_code == 200, r.text
    d = (await client.get(f"/api/orders/{o['id']}", headers=H)).json()
    assert d["estimate_decline_reason"] == "Too expensive for now"
    items = (await client.get("/api/tech/notifications", headers=H)).json()["items"]
    assert any("Too expensive for now" in n["body"] for n in items)


# ── E5: revisions ──
async def test_revision_keeps_history_and_reopens_approval(client):
    H, _, loc, cid = await _shop(client)
    o, tok = await _estimate(client, H, loc, cid, prices=(80,))
    await client.post(f"/api/public/track/{tok}/respond", json={"decision": "decline", "reason": "Cheaper please"})
    assert (await _track(client, tok))["awaiting_approval"] is False

    r = await client.post(f"/api/orders/{o['id']}/estimate/revisions", json={"note": "Used part"}, headers=H)
    assert r.status_code == 201, r.text
    assert r.json()["revision"] == 2
    hist = (await client.get(f"/api/orders/{o['id']}/estimate/revisions", headers=H)).json()["items"]
    assert len(hist) == 1
    assert hist[0]["revision"] == 1 and hist[0]["total"] == 80 and hist[0]["decision"] == "declined"
    assert hist[0]["decline_reason"] == "Cheaper please"
    assert [ln["description"] for ln in hist[0]["lines"]] == ["Work 1"]

    # staff change the price; the customer sees the latest version and can answer again
    d = (await client.get(f"/api/orders/{o['id']}", headers=H)).json()
    await client.patch(f"/api/orders/{o['id']}/lines/{d['lines'][0]['id']}", json={"unit_price": 60}, headers=H)
    d = (await client.get(f"/api/orders/{o['id']}", headers=H)).json()
    assert d["estimate_revision"] == 2 and d["estimate_decline_reason"] is None
    page = await _track(client, tok)
    assert page["revision"] == 2 and page["awaiting_approval"] is True and page["total"] == 60
    r = await client.post(f"/api/public/track/{tok}/respond", json={"decision": "accept", "signature": PNG})
    assert r.status_code == 200, r.text
    # the old version's history is untouched
    hist = (await client.get(f"/api/orders/{o['id']}/estimate/revisions", headers=H)).json()["items"]
    assert [h["total"] for h in hist if h["revision"] == 1] == [80]


async def test_revision_refused_once_converted(client):
    H, _, loc, cid = await _shop(client)
    o, tok = await _estimate(client, H, loc, cid)
    await client.post(f"/api/public/track/{tok}/respond", json={"decision": "accept", "signature": PNG})
    r = await client.post(f"/api/orders/{o['id']}/estimate/revisions", json={}, headers=H)
    assert r.status_code == 409


# ── E5: line-by-line approval ──
async def test_customer_approves_line_by_line(client):
    H, _, loc, cid = await _shop(client)
    o, tok = await _estimate(client, H, loc, cid, prices=(50, 30))
    page = await _track(client, tok)
    assert page["can_choose_lines"] is True
    ids = {ln["label"]: ln["id"] for ln in page["lines"]}
    r = await client.post(f"/api/public/track/{tok}/respond",
                          json={"decision": "accept", "signature": PNG, "skip_line_ids": [ids["Work 2"]]})
    assert r.status_code == 200, r.text
    d = (await client.get(f"/api/orders/{o['id']}", headers=H)).json()
    assert [ln["description"] for ln in d["lines"]] == ["Work 1"]
    assert d["total"] == 50
    # what was offered is kept in the history
    hist = (await client.get(f"/api/orders/{o['id']}/estimate/revisions", headers=H)).json()["items"]
    assert hist and hist[-1]["total"] == 80 and len(hist[-1]["lines"]) == 2
    assert hist[-1]["skipped"] == ["Work 2"]


async def test_customer_cannot_skip_every_line(client):
    H, _, loc, cid = await _shop(client)
    o, tok = await _estimate(client, H, loc, cid, prices=(50, 30))
    ids = [ln["id"] for ln in (await _track(client, tok))["lines"]]
    r = await client.post(f"/api/public/track/{tok}/respond",
                          json={"decision": "accept", "signature": PNG, "skip_line_ids": ids})
    assert r.status_code == 400
    # an id from another job is refused, not ignored
    o2, _ = await _estimate(client, H, loc, cid, prices=(10, 20))
    other = (await client.get(f"/api/orders/{o2['id']}", headers=H)).json()["lines"][0]["id"]
    r = await client.post(f"/api/public/track/{tok}/respond",
                          json={"decision": "accept", "signature": PNG, "skip_line_ids": [other]})
    assert r.status_code == 400
    assert len((await client.get(f"/api/orders/{o2['id']}", headers=H)).json()["lines"]) == 2


# ── E5: deposit at approval ──
async def test_deposit_requested_at_approval_links_to_pay(client):
    H, _, loc, cid = await _shop(client)
    o, tok = await _estimate(client, H, loc, cid, prices=(200,))
    r = await client.patch(f"/api/orders/{o['id']}/deposit", json={"amount": 50}, headers=H)
    assert r.status_code == 200, r.text
    page = await _track(client, tok)
    assert page["deposit"] == {"amount": 50.0, "paid": 0.0, "due": 50.0, "can_pay": False}
    r = await client.post(f"/api/public/track/{tok}/deposit")
    assert r.status_code == 409          # not before the customer approves
    await client.post(f"/api/public/track/{tok}/respond", json={"decision": "accept", "signature": PNG})
    page = await _track(client, tok)
    assert page["deposit"]["can_pay"] is True and page["deposit"]["due"] == 50.0
    r = await client.post(f"/api/public/track/{tok}/deposit")
    assert r.status_code == 201, r.text
    j = r.json()
    assert j["amount"] == 50.0 and j["url"]
    # asking again reuses the same link
    assert (await client.post(f"/api/public/track/{tok}/deposit")).json()["intent_id"] == j["intent_id"]


async def test_deposit_link_needs_a_deposit(client):
    H, _, loc, cid = await _shop(client)
    _, tok = await _estimate(client, H, loc, cid)
    await client.post(f"/api/public/track/{tok}/respond", json={"decision": "accept", "signature": PNG})
    assert (await _track(client, tok))["deposit"] is None
    assert (await client.post(f"/api/public/track/{tok}/deposit")).status_code == 409


# ── Customer approvals of recommended work go through the normal status change ──
async def test_approved_recommendation_moves_the_job_through_the_status_change(client):
    H, _, loc, cid = await _shop(client)
    sitems = await _statuses(client, H)
    in_progress = next(s for s in sitems
                       if s.get("status_group") == "in_progress" and s.get("record_type", "work_order") == "work_order")
    r = await client.put("/api/staff/public-pages", json={"oe_wo_accept_status": in_progress["key"]}, headers=H)
    assert r.status_code == 200, r.text
    o = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x"}, headers=H)).json()
    assert o["status"] != in_progress["key"]
    rec = (await client.post(f"/api/orders/{o['id']}/recommendations", json={"title": "Case", "kind": "custom",
                                                                            "unit_price": 20}, headers=H)).json()
    await client.post(f"/api/orders/{o['id']}/recommendations/{rec['id']}/publish", headers=H)
    tok = (await client.post(f"/api/orders/{o['id']}/public-link", headers=H)).json()["token"]
    r = await client.post(f"/api/public/track/{tok}/recommendations/decide", json={"approve_ids": [rec["id"]]})
    assert r.status_code == 200, r.text
    d = (await client.get(f"/api/orders/{o['id']}", headers=H)).json()
    assert d["status"] == in_progress["key"]
    # it was a real status change: logged like one, with the customer's note
    logs = d.get("status_logs") or d.get("history") or []
    assert any(lg.get("to") == in_progress["key"] and lg.get("note") == "Approved by customer" for lg in logs), logs


# ── J4: tasks on the job ──
async def test_job_tasks_list_carries_job_number_and_ticks_off(client):
    H, me, loc, cid = await _shop(client)
    o = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x"}, headers=H)).json()
    t = (await client.post("/api/tasks", json={"title": "Order the screen", "order_id": o["id"],
                                               "assigned_to_id": me["id"]}, headers=H)).json()
    rows = (await client.get(f"/api/tasks?order_id={o['id']}", headers=H)).json()["items"]
    assert [r["title"] for r in rows] == ["Order the screen"]
    assert rows[0]["order_number"] == o["order_number"]
    r = await client.patch(f"/api/tasks/{t['id']}", json={"status": "done"}, headers=H)
    assert r.json()["status"] == "done" and r.json()["completed_at"]


async def test_task_read_path_never_shows_another_shops_job(client, db):
    H, me, loc, cid = await _shop(client, "Shop A")
    H2, _, loc2, cid2 = await _shop(client, "Shop B")
    other = (await client.post("/api/orders", json={"client_id": cid2, "location_id": loc2, "complaint": "x"},
                               headers=H2)).json()
    # a bad link written straight to the database (the API refuses it): the read path must not follow it
    # (a core insert skips the ORM tenancy guard, like rows from before it existed)
    tid = str(uuid.uuid4())
    await db.execute(insert(Task.__table__).values(id=tid, org_id=me["org_id"], title="Bad link", status="todo",
                                                   priority="0", order_id=other["id"], client_id=cid2))
    await db.commit()
    d = (await client.get(f"/api/tasks/{tid}", headers=H)).json()
    assert d.get("order_number") is None and d.get("client_name") is None
    rows = (await client.get("/api/tasks", headers=H)).json()["items"]
    assert all(r.get("order_number") is None for r in rows if r["id"] == tid)


async def test_task_cannot_link_another_shops_job(client):
    H, _, loc, cid = await _shop(client, "Shop A")
    H2, _, loc2, cid2 = await _shop(client, "Shop B")
    other = (await client.post("/api/orders", json={"client_id": cid2, "location_id": loc2, "complaint": "x"},
                               headers=H2)).json()
    r = await client.post("/api/tasks", json={"title": "x", "order_id": other["id"]}, headers=H)
    assert r.status_code in (403, 404, 422), r.text


# ── J6: job type and priority are editable ──
async def test_job_priority_is_editable(client):
    H, _, loc, cid = await _shop(client)
    o = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x"}, headers=H)).json()
    r = await client.patch(f"/api/orders/{o['id']}/priority", json={"priority": 2}, headers=H)
    assert r.status_code == 200, r.text
    assert (await client.get(f"/api/orders/{o['id']}", headers=H)).json()["priority"] == 2
    r = await client.patch(f"/api/orders/{o['id']}/priority", json={"priority": 7}, headers=H)
    assert r.status_code == 422


async def test_job_type_is_editable_and_listed(client):
    H, _, loc, cid = await _shop(client)
    t = (await client.post("/api/forms/types", json={"name": "Diagnostics"}, headers=H)).json()
    o = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x"}, headers=H)).json()
    r = await client.patch(f"/api/orders/{o['id']}/type", json={"type_id": t["id"]}, headers=H)
    assert r.status_code == 200, r.text
    d = (await client.get(f"/api/orders/{o['id']}", headers=H)).json()
    assert d["type_id"] == t["id"] and d.get("type_name") == "Diagnostics"


# ── J6: running timers stop when the job closes ──
async def test_running_timer_stops_when_the_job_closes(client, db):
    H, me, loc, cid = await _shop(client)
    o = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x"}, headers=H)).json()
    r = await client.post("/api/timeclock/start", json={"order_id": o["id"], "category": "job"}, headers=H)
    assert r.status_code == 201, r.text
    term = next(s for s in await _statuses(client, H)
                if s.get("is_terminal") and s.get("record_type", "work_order") == "work_order"
                and s.get("status_group") == "closed")
    r = await client.patch(f"/api/orders/{o['id']}/status", json={"status": term["key"]}, headers=H)
    assert r.status_code == 200, r.text
    rows = (await db.execute(select(TimeEntry).where(TimeEntry.order_id == o["id"]))).scalars().all()
    await db.refresh(rows[0])
    assert rows and all(e.state == "stopped" and e.ended_at for e in rows)


# ── J6: QuickBooks gets the lines the invoice really bills ──
async def test_qbo_sub_invoice_sends_only_its_lines(client, db):
    from backend.services import qbo
    H, me, loc, cid = await _shop(client)
    o = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x"}, headers=H)).json()
    await _line(client, H, o["id"], "Screen", 100)
    await _line(client, H, o["id"], "Battery", 40)
    lines = (await client.get(f"/api/orders/{o['id']}", headers=H)).json()["lines"]
    screen = next(ln["id"] for ln in lines if ln["description"] == "Screen")
    r = await client.post(f"/api/cash/orders/{o['id']}/invoices", json={"line_ids": [screen]}, headers=H)
    assert r.status_code == 201, r.text
    iid = r.json().get("id") or r.json().get("invoice_id")
    payload = await qbo._build_payload(db, me["org_id"], "invoice", iid)
    assert [ln.get("Description") for ln in payload["Line"]] == ["Screen"]
    assert sum(ln["Amount"] for ln in payload["Line"]) == 100


async def test_qbo_multi_job_invoice_sends_every_jobs_lines(client, db):
    from backend.services import qbo
    H, me, loc, cid = await _shop(client)
    o1 = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x"}, headers=H)).json()
    o2 = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "y"}, headers=H)).json()
    await _line(client, H, o1["id"], "Job A work", 100)
    await _line(client, H, o2["id"], "Job B work", 60)
    r = await client.post("/api/finance/invoices", json={"order_ids": [o1["id"], o2["id"]]}, headers=H)
    assert r.status_code in (200, 201), r.text
    payload = await qbo._build_payload(db, me["org_id"], "invoice", r.json()["id"])
    assert sorted(ln.get("Description") for ln in payload["Line"]) == ["Job A work", "Job B work"]
    assert sum(ln["Amount"] for ln in payload["Line"]) == 160


async def test_qbo_invoice_discount_is_sent(client, db):
    from backend.services import qbo
    H, me, loc, cid = await _shop(client)
    r = await client.post("/api/finance/invoices", json={"client_id": cid, "discount_pct": 10,
                                                         "lines": [{"description": "Labour", "qty": 1, "unit_price": 200}]},
                          headers=H)
    assert r.status_code in (200, 201), r.text
    inv = r.json()
    payload = await qbo._build_payload(db, me["org_id"], "invoice", inv["id"])
    sales = [ln for ln in payload["Line"] if ln["DetailType"] == "SalesItemLineDetail"]
    disc = [ln for ln in payload["Line"] if ln["DetailType"] == "DiscountLineDetail"]
    assert [ln["Description"] for ln in sales] == ["Labour"]
    assert disc and disc[0]["Amount"] == 20


# ── Xero: an honest "not connected" instead of a silent pretend push ──
async def test_xero_push_says_not_connected(client):
    H, _, loc, cid = await _shop(client)
    r = await client.post(f"/api/integrations/xero/sync/contact/{cid}", headers=H)
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] is False and j["error"] == "not_connected" and j.get("message")
    st = (await client.get("/api/integrations/xero/status", headers=H)).json()
    assert st["connected"] is False


async def test_xero_simulated_connection_never_reports_a_push(client, db):
    from backend.models import XeroConnection
    from backend.services import xero
    H, me, loc, cid = await _shop(client)
    db.add(XeroConnection(org_id=me["org_id"], environment="production", connected=True, tenant_id="sandbox-tenant"))
    await db.commit()
    res = await xero.push_entity(db, me["org_id"], "contact", cid)
    assert res["ok"] is False and res["error"] == "not_connected" and "simulated" not in res
    assert (await xero.status(db, me["org_id"]))["connected"] is False


# ── Review follow-ups ──
async def test_qbo_taxed_lines_send_tax_once(client, db):
    from backend.services import qbo
    H, me, loc, cid = await _shop(client)
    o = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x"}, headers=H)).json()
    for desc, mode in (("Screen", "imposed"), ("Glass", "included")):
        r = await client.post(f"/api/orders/{o['id']}/lines", json={"kind": "custom", "line_type": "labour", "description": desc,
                                                                    "qty": 2, "unit_price": 55, "tax_rate": 10, "tax_mode": mode},
                              headers=H)
        assert r.status_code == 201, r.text
    lines = (await client.get(f"/api/orders/{o['id']}", headers=H)).json()["lines"]
    screen = next(ln["id"] for ln in lines if ln["description"] == "Screen")
    sub = (await client.post(f"/api/cash/orders/{o['id']}/invoices", json={"line_ids": [screen]}, headers=H)).json()
    whole = (await client.post("/api/finance/invoices", json={"order_id": o["id"]}, headers=H)).json()
    for iid in (sub["invoice_id"], whole["id"]):
        inv = (await client.get(f"/api/finance/invoices/{iid}", headers=H)).json()
        p = await qbo._build_payload(db, me["org_id"], "invoice", iid)
        sales = [ln for ln in p["Line"] if ln["DetailType"] == "SalesItemLineDetail"]
        for ln in sales:     # QuickBooks: Amount = Qty × UnitPrice, before tax
            d = ln["SalesItemLineDetail"]
            assert abs(ln["Amount"] - d["Qty"] * d["UnitPrice"]) < 0.02
        tax = p.get("TxnTaxDetail", {}).get("TotalTax", 0)
        assert abs(sum(ln["Amount"] for ln in sales) + tax - float(inv["total"])) < 0.02, (p, inv)
    p = await qbo._build_payload(db, me["org_id"], "invoice", sub["invoice_id"])
    assert [ln["Description"] for ln in p["Line"]] == ["Screen"] and p["Line"][0]["Amount"] == 110
    assert p["TxnTaxDetail"]["TotalTax"] == 11


async def test_customer_can_be_asked_to_take_the_whole_estimate(client):
    H, _, loc, cid = await _shop(client)
    r = await client.put("/api/staff/public-pages", json={"oe_estimate_line_choice": False}, headers=H)
    assert r.status_code == 200, r.text
    o, tok = await _estimate(client, H, loc, cid, prices=(50, 30))
    page = await _track(client, tok)
    assert page["can_choose_lines"] is False
    r = await client.post(f"/api/public/track/{tok}/respond",
                          json={"decision": "accept", "signature": PNG, "skip_line_ids": [page["lines"][1]["id"]]})
    assert r.status_code == 403
    assert len((await client.get(f"/api/orders/{o['id']}", headers=H)).json()["lines"]) == 2


async def test_skipped_line_totals_include_tax_and_staff_hear_what_was_left_out(client):
    H, _, loc, cid = await _shop(client)
    o, tok = await _estimate(client, H, loc, cid, prices=(50,))
    await client.post(f"/api/orders/{o['id']}/lines", json={"kind": "custom", "line_type": "labour", "description": "Case",
                                                            "qty": 1, "unit_price": 100, "tax_rate": 10}, headers=H)
    page = await _track(client, tok)
    case = next(ln for ln in page["lines"] if ln["label"] == "Case")
    assert case["total"] == 110
    r = await client.post(f"/api/public/track/{tok}/respond",
                          json={"decision": "accept", "signature": PNG, "skip_line_ids": [case["id"]]})
    assert r.status_code == 200, r.text
    assert (await client.get(f"/api/orders/{o['id']}", headers=H)).json()["total"] == page["total"] - 110
    items = (await client.get("/api/tech/notifications", headers=H)).json()["items"]
    assert any("Left out: Case" in n["body"] for n in items)


async def test_deposit_counts_only_money_paid_since_approval_and_stops_once_invoiced(client):
    H, _, loc, cid = await _shop(client)
    o, tok = await _estimate(client, H, loc, cid, prices=(200,))
    await client.patch(f"/api/orders/{o['id']}/deposit", json={"amount": 50}, headers=H)
    await client.post(f"/api/public/track/{tok}/respond", json={"decision": "accept", "signature": PNG})
    assert (await _track(client, tok))["deposit"]["due"] == 50
    await client.post("/api/finance/invoices", json={"order_id": o["id"]}, headers=H)
    assert (await _track(client, tok))["deposit"]["can_pay"] is False
    assert (await client.post(f"/api/public/track/{tok}/deposit")).status_code == 409


async def test_deposit_link_replaces_a_stale_link(client, db):
    from backend.models import PaymentIntent
    H, _, loc, cid = await _shop(client)
    o, tok = await _estimate(client, H, loc, cid, prices=(200,))
    await client.patch(f"/api/orders/{o['id']}/deposit", json={"amount": 50}, headers=H)
    await client.post(f"/api/public/track/{tok}/respond", json={"decision": "accept", "signature": PNG})
    first = (await client.post(f"/api/public/track/{tok}/deposit")).json()["intent_id"]
    await client.patch(f"/api/orders/{o['id']}/deposit", json={"amount": 60}, headers=H)
    second = (await client.post(f"/api/public/track/{tok}/deposit")).json()
    assert second["intent_id"] != first and second["amount"] == 60
    old = (await db.execute(select(PaymentIntent).where(PaymentIntent.id == first))).scalar_one()
    await db.refresh(old)
    assert old.status not in ("created", "viewed", "pending")


async def test_approved_extra_never_moves_a_job_backwards(client):
    H, _, loc, cid = await _shop(client)
    wo = [s for s in await _statuses(client, H) if s.get("record_type", "work_order") == "work_order"]
    early = next(s for s in wo if s.get("status_group") == "in_progress")
    later = max((s for s in wo if s.get("status_group") in ("in_progress", "ready")
                 and s["sort_order"] > early["sort_order"] and not s.get("is_terminal")), key=lambda s: s["sort_order"])
    await client.put("/api/staff/public-pages", json={"oe_wo_accept_status": early["key"]}, headers=H)
    o = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x"}, headers=H)).json()
    r = await client.patch(f"/api/orders/{o['id']}/status", json={"status": later["key"]}, headers=H)
    assert r.status_code == 200, r.text
    rec = (await client.post(f"/api/orders/{o['id']}/recommendations", json={"title": "Case", "kind": "custom",
                                                                            "unit_price": 20}, headers=H)).json()
    await client.post(f"/api/orders/{o['id']}/recommendations/{rec['id']}/publish", headers=H)
    tok = (await client.post(f"/api/orders/{o['id']}/public-link", headers=H)).json()["token"]
    await client.post(f"/api/public/track/{tok}/recommendations/decide", json={"approve_ids": [rec["id"]]})
    assert (await client.get(f"/api/orders/{o['id']}", headers=H)).json()["status"] == later["key"]


async def test_cancelling_a_job_stops_its_timer_without_billing(client, db):
    H, me, loc, cid = await _shop(client)
    o = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x"}, headers=H)).json()
    await client.post("/api/timeclock/start", json={"order_id": o["id"], "category": "job"}, headers=H)
    dropped = next(s for s in await _statuses(client, H)
                   if s.get("status_group") == "dropped" and s.get("record_type", "work_order") == "work_order")
    r = await client.patch(f"/api/orders/{o['id']}/status", json={"status": dropped["key"], "reason": "Customer left"},
                           headers=H)
    assert r.status_code == 200, r.text
    rows = (await db.execute(select(TimeEntry).where(TimeEntry.order_id == o["id"]))).scalars().all()
    await db.refresh(rows[0])
    assert rows[0].state == "stopped"
