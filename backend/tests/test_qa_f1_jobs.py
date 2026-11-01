"""QA multi-shop run (31 Oct 2026), section F1: jobs, coupons, refunds, numbering and links."""
import pytest

from backend.tests import kit_helpers as K

P = {"line_type": "part", "kind": "product", "description": "Part", "unit_price": 20}


# ── 1. a job only moves to a status from its own pipeline ──────────────────────────────────────
@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["bogus_status", "inv_paid", "po_received", "est_sent", "tr_completed"])
async def test_job_refuses_a_status_outside_the_work_order_pipeline(client, tenant, bad):
    oid, _ = await K.job(client, tenant, await K.loc(client, tenant))
    r = await client.patch(f"/api/orders/{oid}/status", json={"status": bad}, headers=tenant)
    assert r.status_code == 422, r.text
    assert "status" in r.json()["detail"].lower()
    assert (await K.order(client, tenant, oid))["status"] == "new"       # nothing moved


@pytest.mark.asyncio
async def test_estimate_refuses_a_work_order_status(client, tenant):
    await client.get("/api/statuses?is_estimate=true", headers=tenant)
    oid, _ = await K.job(client, tenant, await K.loc(client, tenant), estimate=True)
    r = await client.patch(f"/api/orders/{oid}/status", json={"status": "in_repair"}, headers=tenant)
    assert r.status_code == 422, r.text
    ok = await client.patch(f"/api/orders/{oid}/status", json={"status": "est_sent"}, headers=tenant)
    assert ok.status_code == 200, ok.text


@pytest.mark.asyncio
async def test_job_moves_to_its_own_statuses_even_before_the_board_was_opened(client, tenant):
    oid, _ = await K.job(client, tenant, await K.loc(client, tenant))
    r = await client.patch(f"/api/orders/{oid}/status", json={"status": "in_repair"}, headers=tenant)
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_a_switched_off_status_is_refused(client, tenant):
    items = (await client.get("/api/statuses", headers=tenant)).json()["items"]
    diag = next(s for s in items if s["key"] == "diagnosed")
    assert (await client.delete(f"/api/statuses/{diag['id']}", headers=tenant)).status_code == 200
    oid, _ = await K.job(client, tenant, await K.loc(client, tenant))
    r = await client.patch(f"/api/orders/{oid}/status", json={"status": "diagnosed"}, headers=tenant)
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_allowed_statuses_list_only_the_jobs_pipeline(client, tenant):
    oid, _ = await K.job(client, tenant, await K.loc(client, tenant))
    await client.get("/api/statuses", headers=tenant)
    keys = (await client.get(f"/api/statuses/allowed/{oid}", headers=tenant)).json()["items"]
    assert "in_repair" in keys
    assert not [k for k in keys if k.startswith(("inv_", "po_", "est_", "tr_", "inq_"))], keys


# ── 3. a job line only points at this shop's catalog ───────────────────────────────────────────
async def _other_shop(client):
    import uuid
    r = await client.post("/api/auth/register", json={
        "org_name": "Other Co", "name": "Other", "email": f"o-{uuid.uuid4().hex[:8]}@test.dev",
        "password": "pw-test-12345"})
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    loc = await K.loc(client, h)
    pid = await K.part(client, h, loc, "Their screen", 50, 10, qty=5)
    sid = await K.service(client, h, "Their labour", 40)
    tax = (await client.post("/api/catalog/taxes", json={"name": "Their VAT", "rate": 20}, headers=h)).json()["id"]
    return h, pid, sid, tax


@pytest.mark.asyncio
@pytest.mark.parametrize("which", ["product", "service", "tax"])
async def test_job_line_refuses_another_shops_ids(client, tenant, which):
    oh, pid, sid, tax = await _other_shop(client)
    oid, _ = await K.job(client, tenant, await K.loc(client, tenant))
    body = {"product": {**P, "ref_id": pid},
            "service": {"line_type": "labour", "kind": "service", "description": "S", "ref_id": sid},
            "tax": {**P, "tax_id": tax}}[which]
    r = await client.post(f"/api/orders/{oid}/lines", json=body, headers=tenant)
    assert r.status_code == 404, r.text
    assert await K.lines(client, tenant, oid) == []
    assert await K.stock(client, oh, pid) == 5                         # their stock untouched


@pytest.mark.asyncio
async def test_new_job_refuses_another_shops_product(client, tenant):
    _, pid, _, _ = await _other_shop(client)
    cid = (await client.post("/api/clients", json={"name": "C"}, headers=tenant)).json()["id"]
    r = await client.post("/api/orders", headers=tenant, json={
        "client_id": cid, "location_id": await K.loc(client, tenant), "complaint": "x",
        "lines": [{**P, "ref_id": pid}]})
    assert r.status_code == 404, r.text


@pytest.mark.asyncio
async def test_recommendation_refuses_another_shops_product(client, tenant):
    _, pid, _, _ = await _other_shop(client)
    oid, _ = await K.job(client, tenant, await K.loc(client, tenant))
    r = await client.post(f"/api/orders/{oid}/recommendations", headers=tenant, json={
        "title": "Screen", "kind": "product", "ref_id": pid, "unit_price": 50})
    assert r.status_code == 404, r.text
    ok = await client.post(f"/api/orders/{oid}/recommendations", headers=tenant, json={
        "title": "Screen", "kind": "custom", "unit_price": 50})
    assert ok.status_code in (200, 201), ok.text
    p = await client.patch(f"/api/orders/{oid}/recommendations/{ok.json()['id']}", headers=tenant,
                           json={"kind": "product", "ref_id": pid})
    assert p.status_code == 404, p.text


# ── 4. dropping a job puts its parts back on the shelf, once ───────────────────────────────────
async def _job_with_part(client, h, qty=2, stock=10):
    loc = await K.loc(client, h)
    pid = await K.part(client, h, loc, "Screen", 50, 10, qty=stock)
    oid, cid = await K.job(client, h, loc)
    await K.add_line(client, h, oid, ref_id=pid, qty=qty, **P)
    return oid, pid, cid


@pytest.mark.asyncio
async def test_dropping_a_job_returns_its_parts_once(client, tenant, db):
    from sqlalchemy import select

    from backend.models import StockMovement
    oid, pid, _ = await _job_with_part(client, tenant)
    assert await K.stock(client, tenant, pid) == 8
    r = await client.patch(f"/api/orders/{oid}/status", json={"status": "dropped", "reason": "Customer declined"},
                           headers=tenant)
    assert r.status_code == 200, r.text
    assert await K.stock(client, tenant, pid) == 10
    moves = (await db.execute(select(StockMovement.movement_type, StockMovement.qty).where(
        StockMovement.ref_id == oid, StockMovement.part_id == pid))).all()
    assert sorted(moves) == [("return", 2), ("use", -2)]
    # dropped again, or the line removed afterwards: nothing comes back twice
    await client.patch(f"/api/orders/{oid}/status", json={"status": "dropped"}, headers=tenant)
    line = (await K.lines(client, tenant, oid))[0]
    await client.delete(f"/api/orders/{oid}/lines/{line['id']}", headers=tenant)
    assert await K.stock(client, tenant, pid) == 10


@pytest.mark.asyncio
async def test_dropping_waits_for_nothing_and_reopening_takes_the_parts_again(client, tenant):
    oid, pid, _ = await _job_with_part(client, tenant, qty=3, stock=2)     # 1 unit still on order
    r = await client.patch(f"/api/orders/{oid}/status", json={"status": "dropped"}, headers=tenant)
    assert r.status_code == 200, r.text                                   # pending stock doesn't block a drop
    assert await K.stock(client, tenant, pid) == 2
    r = await client.patch(f"/api/orders/{oid}/status", json={"status": "in_repair"}, headers=tenant)
    assert r.status_code == 200, r.text
    assert await K.stock(client, tenant, pid) == 0                        # back on the job
    assert (await K.lines(client, tenant, oid))[0]["pending_stock"] == 1


@pytest.mark.asyncio
async def test_two_drops_at_once_return_the_parts_once(client, tenant):
    import asyncio
    oid, pid, _ = await _job_with_part(client, tenant)
    rs = await asyncio.gather(*[client.patch(f"/api/orders/{oid}/status", json={"status": "dropped"},
                                             headers=tenant) for _ in range(2)])
    assert all(r.status_code == 200 for r in rs), [r.text for r in rs]
    assert await K.stock(client, tenant, pid) == 10


# ── 5. money given back lowers what the customer is charged ────────────────────────────────────
async def _till(client, h):
    r = await client.post("/api/cash/accounts", json={"name": "Till", "payment_option": "cash"}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _paid_closed_job(client, h, price=100.0, part=False):
    loc = await K.loc(client, h)
    oid, cid = await K.job(client, h, loc)
    pid = None
    if part:
        pid = await K.part(client, h, loc, "Charger", price, 10, qty=5)
        await K.add_line(client, h, oid, ref_id=pid, qty=1, **{**P, "unit_price": price})
    else:
        await K.add_line(client, h, oid, **{**P, "unit_price": price})
    till = await _till(client, h)
    total = (await K.order(client, h, oid))["total"]
    r = await client.post(f"/api/cash/orders/{oid}/pay", json={"method": "cash", "account_id": till, "amount": total},
                          headers=h)
    assert r.status_code in (200, 201), r.text
    r = await client.patch(f"/api/orders/{oid}/status", json={"status": "done"}, headers=h)
    assert r.status_code == 200, r.text
    return oid, cid, pid, till, total


@pytest.mark.asyncio
async def test_goodwill_refund_on_a_paid_job_leaves_nothing_owed(client, tenant, db):
    from backend.services.order_money import balance_due
    oid, cid, _, till, total = await _paid_closed_job(client, tenant)
    r = await client.post("/api/returns/refunds", json={
        "account_id": till, "amount": 25, "order_id": oid, "reason": "Sorry for the wait"}, headers=tenant)
    assert r.status_code == 201, r.text
    o = await K.order(client, tenant, oid)
    assert o["total"] == pytest.approx(total - 25)
    assert any(ln["total"] == -25 and "Sorry for the wait" in ln["description"] for ln in o["lines"])
    bal = (await client.get(f"/api/clients/{cid}", headers=tenant)).json()["balance"]
    assert bal == pytest.approx(0)                              # no debt on the customer's account
    from backend.models import Order
    assert await balance_due(db, await db.get(Order, oid)) == 0


@pytest.mark.asyncio
async def test_return_and_refund_take_the_item_off_the_job(client, tenant):
    oid, cid, pid, till, total = await _paid_closed_job(client, tenant, price=89.95, part=True)
    ret = await client.post("/api/returns", json={"order_id": oid, "client_id": cid, "reason": "faulty",
                                                  "lines": [{"part_id": pid, "qty": 1}]}, headers=tenant)
    assert ret.status_code == 201, ret.text
    r = await client.post("/api/returns/refunds", json={
        "account_id": till, "amount": total, "order_id": oid, "return_id": ret.json()["id"]}, headers=tenant)
    assert r.status_code == 201, r.text
    o = await K.order(client, tenant, oid)
    assert o["total"] == pytest.approx(0)
    assert any("Charger" in ln["description"] and ln["total"] < 0 for ln in o["lines"]), o["lines"]
    assert (await client.get(f"/api/clients/{cid}", headers=tenant)).json()["balance"] == pytest.approx(0)


@pytest.mark.asyncio
async def test_refunding_an_overpayment_leaves_the_job_total(client, tenant, db):
    oid, cid, _, till, total = await _paid_closed_job(client, tenant)
    # 20 more came in than the job costs (paid again by mistake at the counter)
    from backend.models import Payment
    db.add(Payment(order_id=oid, client_id=cid, method="cash", kind="payment", amount=20, account_id=till))
    await db.commit()
    r = await client.post("/api/returns/refunds", json={"account_id": till, "amount": 30, "order_id": oid},
                          headers=tenant)
    assert r.status_code == 201, r.text
    assert (await K.order(client, tenant, oid))["total"] == pytest.approx(total - 10)   # 20 was theirs anyway


@pytest.mark.asyncio
async def test_deleting_a_refund_puts_the_job_total_back(client, tenant):
    oid, cid, _, till, total = await _paid_closed_job(client, tenant)
    rid = (await client.post("/api/returns/refunds", json={"account_id": till, "amount": 25, "order_id": oid},
                             headers=tenant)).json()["id"]
    r = await client.delete(f"/api/returns/refunds/{rid}", headers=tenant)
    assert r.status_code == 200, r.text
    o = await K.order(client, tenant, oid)
    assert o["total"] == pytest.approx(total)
    assert all(ln["total"] >= 0 for ln in o["lines"])
    assert (await client.get(f"/api/clients/{cid}", headers=tenant)).json()["balance"] == pytest.approx(0)


# ── 6. small things a shop notices ─────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_estimates_have_their_own_counter(client, tenant):
    loc = await K.loc(client, tenant)
    nums = []
    for est in (False, True, False, True, False):
        oid, _ = await K.job(client, tenant, loc, estimate=est)
        nums.append((await K.order(client, tenant, oid))["order_number"])
    assert nums[0::2] == ["WO-00001", "WO-00002", "WO-00003"]          # no gaps for the estimates
    assert nums[1::2] == ["E-00001", "E-00002"]


@pytest.mark.asyncio
async def test_estimate_numbering_follows_its_own_setting(client, tenant):
    r = await client.put("/api/settings/numbering", headers=tenant, json={
        "doc_type": "estimate", "template": "QUOTE/{C:3}", "begins_from": 7, "scope_key": "org"})
    assert r.status_code == 200, r.text
    await client.put("/api/settings/numbering", headers=tenant, json={
        "doc_type": "order", "template": "JOB{C:4}", "begins_from": 1, "scope_key": "org"})
    loc = await K.loc(client, tenant)
    est, _ = await K.job(client, tenant, loc, estimate=True)
    assert (await K.order(client, tenant, est))["order_number"] == "QUOTE/007"
    copy = (await client.post(f"/api/orders/{est}/duplicate", headers=tenant)).json()
    assert copy["order_number"] == "QUOTE/008", copy
    await client.get("/api/statuses?is_estimate=true", headers=tenant)
    assert (await client.post(f"/api/orders/{est}/convert", headers=tenant)).status_code == 200
    assert (await K.order(client, tenant, est))["order_number"] == "JOB0001"


@pytest.mark.asyncio
async def test_deposit_is_capped_at_the_job_total(client, tenant):
    oid, _ = await K.job(client, tenant, await K.loc(client, tenant))
    await K.add_line(client, tenant, oid, **{**P, "unit_price": 830})
    total = (await K.order(client, tenant, oid))["total"]
    r = await client.patch(f"/api/orders/{oid}/deposit", json={"amount": total + 70}, headers=tenant)
    assert r.status_code == 422, r.text
    assert "job total" in r.json()["detail"]
    ok = await client.patch(f"/api/orders/{oid}/deposit", json={"amount": total}, headers=tenant)
    assert ok.status_code == 200 and ok.json()["deposit_amount"] == total


@pytest.mark.asyncio
async def test_a_locked_closed_job_refuses_every_edit(client, tenant):
    r = await client.post("/api/statuses", headers=tenant, json={
        "name": "Picked up", "key": "picked_up", "status_group": "closed", "locks_edits": True})
    assert r.status_code == 201, r.text
    oid, _ = await K.job(client, tenant, await K.loc(client, tenant))
    await K.add_line(client, tenant, oid, **P)
    assert (await client.patch(f"/api/orders/{oid}/status", json={"status": "picked_up"},
                               headers=tenant)).status_code == 200
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    edits = [("deposit", {"amount": 5}), ("assign", {"assigned_to_id": me.get("id")}),
             ("custom-data", {"custom_data": {"x": "1"}})]
    for path, body in edits:
        r = await client.patch(f"/api/orders/{oid}/{path}", json=body, headers=tenant)
        assert r.status_code == 409, (path, r.text)
    assert (await client.post(f"/api/orders/{oid}/lines", json=P, headers=tenant)).status_code == 409
    # reopening is how the shop unlocks it
    assert (await client.patch(f"/api/orders/{oid}/status", json={"status": "in_repair"},
                               headers=tenant)).status_code == 200
    assert (await client.patch(f"/api/orders/{oid}/deposit", json={"amount": 5}, headers=tenant)).status_code == 200


@pytest.mark.asyncio
async def test_estimate_public_page_shows_estimate_steps(client, tenant, db):
    await client.get("/api/statuses?is_estimate=true", headers=tenant)
    loc = await K.loc(client, tenant)
    est, _ = await K.job(client, tenant, loc, estimate=True)
    page = await K.track(client, db, est)
    labels = [s["label"] for s in page["stages"]]
    assert "In repair" not in labels and labels[-1] == "Approved"
    assert page["stage_index"] == 0
    await client.patch(f"/api/orders/{est}/status", json={"status": "est_sent"}, headers=tenant)
    assert (await K.track(client, db, est))["stage_index"] == 1
    job, _ = await K.job(client, tenant, loc)
    assert "In repair" in [s["label"] for s in (await K.track(client, db, job))["stages"]]


@pytest.mark.asyncio
async def test_a_new_portal_link_keeps_the_old_one_until_revoked(client, tenant):
    cid = (await client.post("/api/clients", json={"name": "Pat"}, headers=tenant)).json()["id"]
    old = (await client.post(f"/api/clients/{cid}/portal-link", headers=tenant)).json()["portal_token"]
    new = (await client.post(f"/api/clients/{cid}/portal-link", headers=tenant)).json()["portal_token"]
    assert (await client.get(f"/api/public/portal/{old}")).status_code == 200      # still in their inbox
    assert (await client.get(f"/api/public/portal/{new}")).status_code == 200
    assert (await client.post(f"/api/clients/{cid}/portal-link/revoke", headers=tenant)).status_code == 200
    assert (await client.get(f"/api/public/portal/{old}")).status_code == 404
    assert (await client.get(f"/api/public/portal/{new}")).status_code == 404


@pytest.mark.asyncio
async def test_two_quick_adds_of_one_part_make_one_line(client, tenant):
    import asyncio
    loc = await K.loc(client, tenant)
    pid = await K.part(client, tenant, loc, "Battery", 30, 10, qty=10)
    oid, _ = await K.job(client, tenant, loc)
    rs = await asyncio.gather(*[client.post(f"/api/orders/{oid}/lines", headers=tenant,
                                            json={**P, "ref_id": pid, "qty": 1}) for _ in range(2)])
    assert all(r.status_code == 201 for r in rs), [r.text for r in rs]
    lines = await K.lines(client, tenant, oid)
    assert len(lines) == 1 and lines[0]["qty"] == 2
    assert await K.stock(client, tenant, pid) == 8


# ── review follow-ups ──────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_refund_line_belongs_to_the_refund(client, tenant):
    oid, _, _, till, _ = await _paid_closed_job(client, tenant)
    await client.post("/api/returns/refunds", json={"account_id": till, "amount": 25, "order_id": oid}, headers=tenant)
    line = next(ln for ln in (await K.order(client, tenant, oid))["lines"] if ln["total"] < 0)
    assert (await client.delete(f"/api/orders/{oid}/lines/{line['id']}", headers=tenant)).status_code in (403, 409)
    assert (await client.patch(f"/api/orders/{oid}/lines/{line['id']}", json={"qty": 2},
                               headers=tenant)).status_code in (403, 409)
    copy = (await client.post(f"/api/orders/{oid}/duplicate", headers=tenant)).json()
    assert all(ln["total"] >= 0 for ln in (await K.order(client, tenant, copy["id"]))["lines"])


@pytest.mark.asyncio
async def test_dropping_a_finished_job_returns_nothing(client, tenant):
    oid, pid, _ = await _job_with_part(client, tenant)
    assert (await client.patch(f"/api/orders/{oid}/status", json={"status": "delivered"},
                               headers=tenant)).status_code == 200
    assert (await client.patch(f"/api/orders/{oid}/status", json={"status": "dropped"},
                               headers=tenant)).status_code == 200
    assert await K.stock(client, tenant, pid) == 8          # the parts left inside the device


@pytest.mark.asyncio
async def test_a_dropped_job_can_be_closed_straight_away_again(client, tenant):
    oid, pid, _ = await _job_with_part(client, tenant)
    await client.patch(f"/api/orders/{oid}/status", json={"status": "dropped"}, headers=tenant)
    r = await client.patch(f"/api/orders/{oid}/status", json={"status": "done"}, headers=tenant)
    assert r.status_code == 200, r.text
    assert await K.stock(client, tenant, pid) == 8


@pytest.mark.asyncio
async def test_a_deposit_can_be_asked_at_check_in(client, tenant):
    oid, _ = await K.job(client, tenant, await K.loc(client, tenant))
    r = await client.patch(f"/api/orders/{oid}/deposit", json={"amount": 50}, headers=tenant)
    assert r.status_code == 200, r.text
