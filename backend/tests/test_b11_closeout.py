"""B11 — a job can be closed out cleanly: the comment gate can be met with the change, optional
checklist items don't hold a job back, a job can't leave with money owed unless a manager says
so, timers stop when the job does, "Finish & take" never takes money for a close that fails, and
the next-step suggestion follows the main path instead of detouring to "Waiting parts"."""
import uuid

import pytest


async def _loc(client, H):
    d = (await client.get("/api/staff/locations", headers=H)).json()
    return (d if isinstance(d, list) else d["items"])[0]["id"]


async def _order(client, H, price=0):
    cid = (await client.post("/api/clients", json={"name": "Neha"}, headers=H)).json()["id"]
    o = (await client.post("/api/orders", json={
        "client_id": cid, "location_id": await _loc(client, H), "complaint": "screen"}, headers=H)).json()
    if price:
        r = await client.post(f"/api/orders/{o['id']}/lines", json={
            "line_type": "labour", "kind": "service", "description": "Screen swap",
            "qty": 1, "unit_price": price}, headers=H)
        assert r.status_code == 201, r.text
    return o


async def _emp_with_role(client, H, perms):
    role = (await client.post("/api/staff/roles", json={"name": f"R{uuid.uuid4().hex[:4]}", "permissions": perms},
                              headers=H)).json()
    email = f"e-{uuid.uuid4().hex[:6]}@t.dev"
    await client.post("/api/staff/employees", json={
        "name": "Emp", "email": email, "password": "pw-test-12345", "role_id": role["id"]}, headers=H)
    tok = (await client.post("/api/auth/token", data={"username": email, "password": "pw-test-12345"})).json()
    return {"Authorization": f"Bearer {tok['access_token']}"}


async def _cash_account(client, H):
    await client.post("/api/cash/accounts", json={"name": "Till", "payment_option": "cash"}, headers=H)
    return [a for a in (await client.get("/api/cash/accounts", headers=H)).json()["items"] if a["name"] == "Till"][0]["id"]


async def _balance(client, H, oid):
    return (await client.get(f"/api/cash/orders/{oid}/payment-context", headers=H)).json()["remaining"]


async def _attach(client, H, oid, items):
    tpl = (await client.post("/api/checklist-templates", json={
        "name": f"QC {uuid.uuid4().hex[:4]}", "steps": [{"title": "QC", "sections": [{"title": "Checks", "items": items}]}]},
        headers=H)).json()
    cl = (await client.post(f"/api/orders/{oid}/checklists", json={"template_id": tpl["id"]}, headers=H)).json()
    return cl["id"], (await client.get(f"/api/orders/{oid}/checklists/{cl['id']}", headers=H)).json()["items"]


# ── the comment gate can be met from the drawer ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_comment_gate_is_met_by_a_comment_sent_with_the_change(client, tenant):
    H = tenant
    key = (await client.post("/api/statuses", json={
        "name": "Quoted", "status_group": "in_progress", "require_comment": True}, headers=H)).json()["key"]
    o = await _order(client, H)

    # Without one: refused with a code the drawer uses to open the comment box.
    r = await client.patch(f"/api/orders/{o['id']}/status", json={"status": key}, headers=H)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "comment_required"
    chk = (await client.post(f"/api/orders/{o['id']}/status/check", json={"status": key}, headers=H)).json()
    assert chk["ok"] is False and chk["code"] == "comment_required"

    # With one: the comment is saved and the status moves, in one step.
    r = await client.patch(f"/api/orders/{o['id']}/status",
                           json={"status": key, "comment": "Quoted 4,500 for the screen"}, headers=H)
    assert r.status_code == 200, r.text
    got = (await client.get(f"/api/orders/{o['id']}", headers=H)).json()
    assert got["status"] == key
    assert [c["body"] for c in got["comments"]] == ["Quoted 4,500 for the screen"]


# ── optional checklist items don't block "Ready" ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_only_required_checklist_items_block_a_checklist_status(client, tenant):
    H = tenant
    sts = (await client.get("/api/statuses?record_type=work_order", headers=H)).json()
    ready = next(s for s in (sts.get("items") if isinstance(sts, dict) else sts) if s["key"] == "ready")
    assert (await client.put(f"/api/statuses/{ready['id']}", json={**ready, "require_checklist": True},
                             headers=H)).status_code == 200
    o = await _order(client, H)
    _, items = await _attach(client, H, o["id"], [
        {"label": "Screen works", "field_kind": "check_result", "required": True},
        {"label": "Wipe the case", "field_kind": "check_result", "required": False}])
    req = next(i for i in items if i["label"] == "Screen works")
    cid = items[0].get("checklist_id")

    r = await client.patch(f"/api/orders/{o['id']}/status", json={"status": "ready"}, headers=H)
    assert r.status_code == 409 and "required" in r.text   # the required one is still open

    cl = (await client.get(f"/api/orders/{o['id']}/checklists", headers=H)).json()
    cid = cid or (cl.get("items") if isinstance(cl, dict) else cl)[0]["id"]
    assert (await client.patch(f"/api/orders/{o['id']}/checklists/{cid}/items/{req['id']}",
                               json={"value": "pass"}, headers=H)).status_code == 200
    # The optional item is still open: it must not hold the job back.
    r = await client.patch(f"/api/orders/{o['id']}/status", json={"status": "ready"}, headers=H)
    assert r.status_code == 200, r.text


# ── a job can't leave with money owed ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_delivered_with_money_owed_is_refused_with_a_clear_message(client, tenant):
    H = tenant
    o = await _order(client, H, price=4500)
    r = await client.patch(f"/api/orders/{o['id']}/status", json={"status": "delivered"}, headers=H)
    assert r.status_code == 409, r.text
    d = r.json()["detail"]
    assert d["code"] == "unpaid" and d["owed"] == 4500 and d["can_override"] is True
    assert "4,500.00" in d["message"] and "Delivered" in d["message"]
    assert (await client.get(f"/api/orders/{o['id']}", headers=H)).json()["status"] == "new"


@pytest.mark.asyncio
async def test_a_manager_can_close_unpaid_on_purpose(client, tenant):
    H = tenant
    o = await _order(client, H, price=4500)
    r = await client.patch(f"/api/orders/{o['id']}/status",
                           json={"status": "delivered", "allow_unpaid": True}, headers=H)
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_staff_without_finance_cannot_override_the_unpaid_block(client, tenant):
    H = tenant
    o = await _order(client, H, price=4500)
    T = await _emp_with_role(client, H, {"can_close_tickets": True, "can_view_finance": False})
    r = await client.patch(f"/api/orders/{o['id']}/status", json={"status": "delivered"}, headers=T)
    assert r.status_code == 409
    assert r.json()["detail"]["can_override"] is False
    assert "manager" in r.json()["detail"]["message"]
    r = await client.patch(f"/api/orders/{o['id']}/status",
                           json={"status": "delivered", "allow_unpaid": True}, headers=T)
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_done_stays_closable_unpaid_so_it_can_be_invoiced(client, tenant):
    H = tenant
    o = await _order(client, H, price=4500)
    r = await client.patch(f"/api/orders/{o['id']}/status", json={"status": "done"}, headers=H)
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_a_paid_job_closes_without_any_override(client, tenant):
    H = tenant
    o = await _order(client, H, price=200)
    aid = await _cash_account(client, H)
    assert (await client.post(f"/api/cash/orders/{o['id']}/pay", json={
        "method": "cash", "account_id": aid, "amount": 200}, headers=H)).status_code == 200
    r = await client.patch(f"/api/orders/{o['id']}/status", json={"status": "delivered"}, headers=H)
    assert r.status_code == 200, r.text


# ── timers stop when the job is done ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_running_timer_stops_when_the_job_is_done(client, tenant):
    H = tenant
    o = await _order(client, H)
    r = await client.post("/api/timeclock/start", json={"order_id": o["id"]}, headers=H)
    assert r.status_code == 201, r.text
    assert (await client.get("/api/timeclock/active", headers=H)).json()["active"]

    r = await client.patch(f"/api/orders/{o['id']}/status", json={"status": "done"}, headers=H)
    assert r.status_code == 200, r.text
    assert (await client.get("/api/timeclock/active", headers=H)).json()["active"] is None


@pytest.mark.asyncio
async def test_a_refused_close_leaves_the_timer_running(client, tenant):
    H = tenant
    o = await _order(client, H, price=100)
    await client.post("/api/timeclock/start", json={"order_id": o["id"]}, headers=H)
    r = await client.patch(f"/api/orders/{o['id']}/status", json={"status": "delivered"}, headers=H)
    assert r.status_code == 409
    assert (await client.get("/api/timeclock/active", headers=H)).json()["active"]


# ── Tekly Go: "Finish & take" ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_finish_and_take_payment_closes_and_pays_in_one_step(client, tenant):
    H = tenant
    o = await _order(client, H, price=178)
    aid = await _cash_account(client, H)
    r = await client.post(f"/api/orders/{o['id']}/finish", json={
        "payment": {"method": "cash", "account_id": aid, "amount": 178}}, headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "done" and r.json()["payment"]["amount"] == 178
    assert (await client.get(f"/api/orders/{o['id']}", headers=H)).json()["status"] == "done"
    assert await _balance(client, H, o["id"]) == 0


@pytest.mark.asyncio
async def test_finish_that_cannot_close_takes_no_money(client, tenant):
    """The reviewer's case: the cash was taken, then the close came back 409."""
    H = tenant
    o = await _order(client, H, price=178)
    await _attach(client, H, o["id"], [{"label": "Screen works", "field_kind": "check_result", "required": True}])
    aid = await _cash_account(client, H)

    chk = (await client.post(f"/api/orders/{o['id']}/status/check?assume_paid=true",
                             json={"status": "done"}, headers=H)).json()
    assert chk["ok"] is False and "checklist" in chk["message"]

    r = await client.post(f"/api/orders/{o['id']}/finish", json={
        "payment": {"method": "cash", "account_id": aid, "amount": 178}}, headers=H)
    assert r.status_code == 409, r.text
    assert await _balance(client, H, o["id"]) == 178          # no money taken
    assert (await client.get(f"/api/orders/{o['id']}", headers=H)).json()["status"] == "new"


@pytest.mark.asyncio
async def test_finish_with_a_part_payment_rolls_the_payment_back(client, tenant):
    H = tenant
    o = await _order(client, H, price=178)
    aid = await _cash_account(client, H)
    r = await client.post(f"/api/orders/{o['id']}/finish", json={"status": "delivered",
        "payment": {"method": "cash", "account_id": aid, "amount": 100}}, headers=H)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "unpaid"
    assert await _balance(client, H, o["id"]) == 178


@pytest.mark.asyncio
async def test_finish_replays_a_retried_tap_without_paying_twice(client, tenant):
    H = tenant
    o = await _order(client, H, price=50)
    aid = await _cash_account(client, H)
    body = {"payment": {"method": "cash", "account_id": aid, "amount": 50}}
    K = {**H, "Idempotency-Key": uuid.uuid4().hex}
    a = await client.post(f"/api/orders/{o['id']}/finish", json=body, headers=K)
    b = await client.post(f"/api/orders/{o['id']}/finish", json=body, headers=K)
    assert a.status_code == 200 and b.status_code == 200 and a.json() == b.json()
    assert await _balance(client, H, o["id"]) == 0


# ── the next-step button follows the main path ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_my_work_next_step_skips_waiting_parts(client, tenant):
    H = tenant
    me = (await client.get("/api/auth/me", headers=H)).json()
    o = await _order(client, H)
    await client.post(f"/api/orders/{o['id']}/lines", json={
        "line_type": "labour", "kind": "service", "description": "Diag",
        "qty": 1, "unit_price": 0, "technician_id": me["id"], "booked_hours": 1.0}, headers=H)
    assert (await client.patch(f"/api/orders/{o['id']}/status", json={"status": "diagnosed"}, headers=H)).status_code == 200
    card = next(c for c in (await client.get("/api/timeclock/my-work", headers=H)).json()["orders"]
                if c["order_id"] == o["id"])
    assert card["next_status"] == "in_repair", card
