"""End-to-end: estimate → customer approves online → work order → work (tasks, photos, notes,
messages, statuses) → ready → invoice → payment → pickup.

One shop, one customer, one phone repair, through the same API the web app uses. Each step
records what happened; the test fails with the full list of broken steps rather than stopping at
the first, so one run shows every gap in the flow.
"""
import uuid

import pytest

PNG = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGA"
       "hKmMIQAAAABJRU5ErkJggg==")


async def test_estimate_to_paid_pickup(client):
    problems: list[str] = []
    log: list[str] = []

    def check(ok, step, detail=""):
        log.append(f"{'ok  ' if ok else 'FAIL'} {step} {detail}".rstrip())
        print(log[-1])
        if not ok:
            problems.append(f"{step}: {detail}")
        return ok

    r = await client.post("/api/auth/register", json={
        "org_name": "Flow Shop", "name": "Owner", "email": f"flow-{uuid.uuid4().hex[:8]}@t.dev",
        "password": "pw-test-12345"})
    H = {"Authorization": f"Bearer {r.json()['access_token']}"}
    loc = (await client.get("/api/staff/locations", headers=H)).json()["items"][0]["id"]
    me = (await client.get("/api/auth/me", headers=H)).json()

    # ── customer, device, catalog ──
    c = (await client.post("/api/clients", json={"name": "Dana Customer", "phone": "+15550101010",
                                                 "email": "dana@example.com"}, headers=H)).json()
    dev = (await client.post("/api/assets", json={"owner_type": "client", "client_id": c["id"], "brand": "Apple",
                                                  "model": "iPhone 13", "imei": "490154203237518"},
                             headers=H)).json()
    part = (await client.post("/api/catalog/products", json={"name": "iPhone 13 screen", "sell_price": 120,
                                                             "cost_price": 60, "warranty_days": 90}, headers=H)).json()
    await client.post("/api/inventory/parts/" + part["id"] + "/adjust", json={"location_id": loc, "qty": 5,
                                                                             "movement_type": "adjust"}, headers=H)
    svc = (await client.post("/api/catalog/services", json={"name": "Screen fitting", "price": 60}, headers=H)).json()

    # ── 1. estimate ──
    r = await client.post("/api/orders", json={"client_id": c["id"], "device_id": dev["id"], "location_id": loc,
                                               "complaint": "Cracked screen", "is_estimate": True}, headers=H)
    check(r.status_code in (200, 201), "create estimate", r.text[:150])
    est = r.json()
    oid = est["id"]
    check(est.get("is_estimate") is True or str(est.get("order_number", "")).startswith("E"),
          "estimate is marked as an estimate", str({k: est.get(k) for k in ("is_estimate", "order_number")}))
    r1 = await client.post(f"/api/orders/{oid}/lines", json={"kind": "product", "line_type": "part", "ref_id": part["id"],
                                                             "description": part["name"], "qty": 1, "unit_price": 120}, headers=H)
    r2 = await client.post(f"/api/orders/{oid}/lines", json={"kind": "service", "line_type": "labour", "ref_id": svc["id"],
                                                             "description": svc["name"], "qty": 1, "unit_price": 60}, headers=H)
    check(r1.status_code in (200, 201) and r2.status_code in (200, 201), "add estimate lines", f"{r1.status_code}/{r2.status_code}")
    o = (await client.get(f"/api/orders/{oid}", headers=H)).json()
    check(abs(float(o.get("total") or 0) - 180) < 0.01, "estimate total = 180", str(o.get("total")))
    stock = (await client.get(f"/api/inventory/parts/{part['id']}/stock", headers=H)).json()
    check(stock.get("total_available") == 5, "estimate does not take stock", str(stock.get("total_available")))

    # ── 2. send to customer ──
    r = await client.post(f"/api/orders/{oid}/public-link", headers=H)
    check(r.status_code == 200, "customer link", r.text[:120])
    link = r.json()
    token = link.get("token") or (link.get("url") or "").rstrip("/").split("/")[-1]
    r = await client.post(f"/api/orders/{oid}/notify", json={"channel": "email", "body": "Your estimate is ready: please review and approve."}, headers=H)
    check(r.status_code in (200, 201, 202), "send estimate to customer (notify)", f"{r.status_code} {r.text[:150]}")

    # ── 3. customer opens the page and approves with a signature ──
    r = await client.get(f"/api/public/track/{token}")
    check(r.status_code == 200, "customer can open the estimate page", r.text[:120])
    page = r.json() if r.status_code == 200 else {}
    check(bool(page.get("lines") or page.get("items") or page.get("order", {}).get("lines")),
          "customer page shows the priced lines", str(list(page.keys()))[:200])
    r = await client.post(f"/api/public/track/{token}/respond", json={"decision": "accept", "signature": PNG,
                                                                     "name": "Dana Customer"})
    check(r.status_code == 200, "customer approves estimate", r.text[:200])
    o = (await client.get(f"/api/orders/{oid}", headers=H)).json()
    check(o.get("is_estimate") is False, "approved estimate became a work order",
          str({k: o.get(k) for k in ("is_estimate", "order_number", "status")}))
    check(str(o.get("order_number", "")).startswith("WO"), "work order got a WO number", str(o.get("order_number")))
    check(len(o.get("lines") or []) == 2, "lines carried over", str(len(o.get("lines") or [])))
    stock = (await client.get(f"/api/inventory/parts/{part['id']}/stock", headers=H)).json()
    check(stock.get("total_available") == 4, "work order takes the part from stock", str(stock.get("total_available")))

    # ── 4. the work ──
    r = await client.post("/api/tasks", json={"title": "Order screen", "order_id": oid, "assigned_to_id": me["id"]},
                          headers=H)
    check(r.status_code == 201, "task on the job", r.text[:120])
    tasks = (await client.get(f"/api/tasks?order_id={oid}", headers=H)).json()
    titems = [t for t in (tasks.get("items", []) if isinstance(tasks, dict) else tasks) if isinstance(t, dict)]
    check(any(t.get("title") == "Order screen" for t in (titems or [])), "job's tasks list shows it", str(len(titems or [])))
    r = await client.post(f"/api/feed/order/{oid}", json={"item_type": "photo", "media_data": PNG,
                                                          "media_name": "before.png", "visibility": "client"}, headers=H)
    check(r.status_code == 201, "photo on the job", r.text[:120])
    r = await client.post(f"/api/feed/order/{oid}", json={"item_type": "comment", "body": "Screen ordered",
                                                          "visibility": "private"}, headers=H)
    check(r.status_code == 201, "internal note", r.text[:120])
    r = await client.post(f"/api/orders/{oid}/thread", json={"body": "Hi Dana, part arrives tomorrow."}, headers=H)
    if r.status_code == 405 or r.status_code == 404:
        r = await client.post(f"/api/orders/{oid}/customer-reply", json={"body": "Hi Dana, part arrives tomorrow."},
                              headers=H)
    check(r.status_code in (200, 201), "message the customer from the job", f"{r.status_code} {r.text[:150]}")
    page = (await client.get(f"/api/public/track/{token}")).json()
    feed = page.get("updates") or page.get("feed") or []
    check(any(u.get("type") == "photo" for u in feed), "customer page shows the shared photo", str(len(feed)))
    check(not any("Screen ordered" in str(u.get("body", "")) for u in feed), "internal note stays private")
    r = await client.post(f"/api/public/track/{token}/message", json={"body": "Thanks! When can I collect?"})
    check(r.status_code in (200, 201), "customer replies from their page", f"{r.status_code} {r.text[:120]}")

    # ── 5. statuses ──
    sts = (await client.get("/api/statuses", headers=H)).json()
    sitems = sts.get("items", sts) if isinstance(sts, dict) else sts
    by_group = {}
    for s in sitems or []:
        by_group.setdefault(s.get("status_group"), s.get("key"))
    for grp in ("in_progress", "ready"):
        key = by_group.get(grp)
        if not check(bool(key), f"a '{grp}' status exists"):
            continue
        r = await client.patch(f"/api/orders/{oid}/status", json={"status": key}, headers=H)
        check(r.status_code == 200, f"move job to {grp}", f"{r.status_code} {r.text[:200]}")

    # ── 6. invoice ──
    invs = (await client.get(f"/api/cash/orders/{oid}/invoices", headers=H)).json()
    ilist = invs.get("invoices", []) if isinstance(invs, dict) else invs
    if not ilist:
        r = await client.post("/api/finance/invoices", json={"order_id": oid}, headers=H)
        check(r.status_code == 201, "create invoice from job", f"{r.status_code} {r.text[:200]}")
        invs = (await client.get(f"/api/cash/orders/{oid}/invoices", headers=H)).json()
        ilist = invs.get("invoices", []) if isinstance(invs, dict) else invs
    check(bool(ilist), "job has an invoice", str(invs)[:150])
    inv = (ilist or [{}])[0]
    check(abs(float(inv.get("total") or 0) - 180) < 0.01, "invoice total matches the job", str(inv.get("total")))
    if inv.get("id"):
        r = await client.post(f"/api/finance/invoices/{inv['id']}/send", json={"channel": "email"}, headers=H)
        check(r.status_code in (200, 201, 202), "email the invoice", f"{r.status_code} {r.text[:200]}")

    # ── 7. payment and pickup ──
    await client.post("/api/cash/accounts", json={"name": "Till", "payment_option": "cash"}, headers=H)
    acc = (await client.get("/api/cash/accounts", headers=H)).json()["items"][0]["id"]
    r = await client.post(f"/api/cash/orders/{oid}/pay", json={"method": "cash", "account_id": acc}, headers=H)
    check(r.status_code in (200, 201), "take payment", f"{r.status_code} {r.text[:200]}")
    ctx = (await client.get(f"/api/cash/orders/{oid}/payment-context", headers=H)).json()
    check(float(ctx.get("balance_due", ctx.get("remaining", 1)) or 0) == 0, "nothing left to pay", str(ctx)[:200])
    invs = (await client.get(f"/api/cash/orders/{oid}/invoices", headers=H)).json()
    ilist = invs.get("invoices", []) if isinstance(invs, dict) else invs
    check(bool(ilist) and (ilist[0].get("status") == "paid" or ilist[0].get("paid")), "invoice shows paid",
          str((ilist or [{}])[0].get("status")))
    closed = by_group.get("closed")
    if check(bool(closed), "a 'closed' status exists"):
        r = await client.patch(f"/api/orders/{oid}/status", json={"status": closed}, headers=H)
        check(r.status_code == 200, "close the job on pickup", f"{r.status_code} {r.text[:200]}")
    hist = (await client.get(f"/api/feed/order/{oid}", headers=H)).json()
    hitems = hist.get("items", hist) if isinstance(hist, dict) else hist
    check(len(hitems or []) >= 4, "job history records the steps", str(len(hitems or [])))

    assert not problems, "Broken steps:\n- " + "\n- ".join(problems)


# ── Known gaps (flow check 30 Oct 2026, docs/audits/2026-10-30-estimate-to-invoice-flow.md) ──
# Each is expected to fail today; strict, so the test turns red the day it gets fixed and the
# xfail mark must be removed.
async def _shop(client):
    r = await client.post("/api/auth/register", json={"org_name": "Gap", "name": "Owner",
                                                      "email": f"gap-{uuid.uuid4().hex[:8]}@t.dev", "password": "pw-test-12345"})
    H = {"Authorization": f"Bearer {r.json()['access_token']}"}
    loc = (await client.get("/api/staff/locations", headers=H)).json()["items"][0]["id"]
    c = (await client.post("/api/clients", json={"name": "Dana", "phone": "+15550101010", "email": "dana@example.com"},
                           headers=H)).json()
    await client.post("/api/cash/accounts", json={"name": "Till", "payment_option": "cash"}, headers=H)
    acc = (await client.get("/api/cash/accounts", headers=H)).json()["items"][0]["id"]
    return H, loc, c["id"], acc


@pytest.mark.xfail(strict=True, reason="G1: nobody on staff is told when a customer approves an estimate online")
async def test_gap_staff_told_when_customer_approves(client):
    H, loc, cid, _ = await _shop(client)
    o = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x",
                                                "is_estimate": True}, headers=H)).json()
    await client.post(f"/api/orders/{o['id']}/lines", json={"kind": "custom", "line_type": "labour",
                                                            "description": "w", "qty": 1, "unit_price": 50}, headers=H)
    tok = (await client.post(f"/api/orders/{o['id']}/public-link", headers=H)).json()["token"]
    await client.post(f"/api/public/track/{tok}/respond", json={"decision": "accept", "signature": PNG, "name": "Dana"})
    items = (await client.get("/api/tech/notifications", headers=H)).json().get("items", [])
    assert any("approv" in (str(n.get("title", "")) + str(n.get("body", ""))).lower() for n in items)


@pytest.mark.xfail(strict=True, reason="G2: a job line doesn't take its product's warranty, so no warranty starts at pickup")
async def test_gap_warranty_from_catalog_starts_at_pickup(client):
    H, loc, cid, acc = await _shop(client)
    part = (await client.post("/api/catalog/products", json={"name": "Screen", "sell_price": 100, "warranty_days": 90},
                              headers=H)).json()
    o = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x"}, headers=H)).json()
    await client.post(f"/api/orders/{o['id']}/lines", json={"kind": "product", "line_type": "part", "ref_id": part["id"],
                                                            "description": "Screen", "qty": 1, "unit_price": 100}, headers=H)
    await client.post(f"/api/cash/orders/{o['id']}/pay", json={"method": "cash", "account_id": acc}, headers=H)
    sts = (await client.get("/api/statuses", headers=H)).json()
    closed = next(s["key"] for s in (sts.get("items", sts) if isinstance(sts, dict) else sts) if s.get("status_group") == "closed")
    await client.patch(f"/api/orders/{o['id']}/status", json={"status": closed}, headers=H)
    assert (await client.get(f"/api/orders/{o['id']}", headers=H)).json().get("warranty_until")


@pytest.mark.xfail(strict=True, reason="G3: a message to the customer from the job is saved but never sent by SMS/email")
async def test_gap_message_to_customer_is_delivered(client, capsys):
    H, loc, cid, _ = await _shop(client)
    o = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x"}, headers=H)).json()
    capsys.readouterr()
    r = await client.post(f"/api/orders/{o['id']}/thread", json={"body": "Your phone is ready"}, headers=H)
    assert r.status_code in (200, 201)
    out = capsys.readouterr().out          # simulated sends print "[notify:<channel>:simulated] -> …"
    assert any("[notify:" in ln and "Your phone is ready" in ln for ln in out.splitlines())


async def test_gap_deposit_reaches_the_invoice(client):
    H, loc, cid, acc = await _shop(client)
    o = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x"}, headers=H)).json()
    await client.post(f"/api/orders/{o['id']}/lines", json={"kind": "custom", "line_type": "labour", "description": "w",
                                                            "qty": 1, "unit_price": 200}, headers=H)
    await client.post(f"/api/cash/orders/{o['id']}/prepay", json={"amount": 50, "account_id": acc}, headers=H)
    await client.post("/api/finance/invoices", json={"order_id": o["id"]}, headers=H)
    await client.post(f"/api/cash/orders/{o['id']}/pay", json={"method": "cash", "account_id": acc}, headers=H)
    inv = (await client.get(f"/api/cash/orders/{o['id']}/invoices", headers=H)).json()["invoices"][0]
    assert inv["status"] == "paid" and float(inv["amount_paid"]) == 200
