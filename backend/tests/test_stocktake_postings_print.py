"""Builds 91-95 — stocktake depth, scheduler/appointments, posting documents,
transfer receive, suppliers & PO reservations, and printing."""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from backend.models import (
    Part,
    POLine,
    Posting,
    PurchaseOrder,
    StockLevel,
    StockReservation,
    StockTransfer,
    StockTransferLine,
    Supplier,
)


async def _me(client, H):
    return (await client.get("/api/auth/me", headers=H)).json()


async def _loc(client, H):
    return (await client.get("/api/staff/locations", headers=H)).json()["items"][0]["id"]


async def _wh(client, H):
    items = (await client.get("/api/settings/warehouses?kind=product", headers=H)).json()["items"]
    return next(w for w in items if w["is_default"])


async def _part(client, H, name="Widget", sku="WS-1", barcode=None, cost=5, sell=10):
    return (await client.post("/api/inventory/parts", json={
        "name": name, "sku": sku, "barcode": barcode, "cost_price": cost,
        "sell_price": sell}, headers=H)).json()


async def _stock(client, H, part_id, qty):
    loc = await _loc(client, H)
    r = await client.post(f"/api/inventory/parts/{part_id}/adjust", json={
        "location_id": loc, "qty": qty, "movement_type": "receive"}, headers=H)
    assert r.status_code in (200, 201), r.text


# ══ Build 91: stocktake depth ════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_full_count_seeds_lines_partial_does_not(client, tenant):
    w = await _wh(client, tenant)
    p = await _part(client, tenant)
    await _stock(client, tenant, p["id"], 10)

    full = await client.post("/api/stocktake", json={
        "warehouse_id": w["id"], "kind": "full"}, headers=tenant)
    assert full.status_code == 201, full.text
    assert full.json()["lines"] == 1          # seeded from what the warehouse holds

    partial = await client.post("/api/stocktake", json={
        "warehouse_id": w["id"], "kind": "partial"}, headers=tenant)
    assert partial.json()["lines"] == 0       # you count only what you scan


@pytest.mark.asyncio
async def test_scan_increments_and_flags_unknown(client, tenant):
    w = await _wh(client, tenant)
    p = await _part(client, tenant, barcode="BC-1")
    await _stock(client, tenant, p["id"], 5)
    st = (await client.post("/api/stocktake", json={
        "warehouse_id": w["id"], "kind": "full"}, headers=tenant)).json()

    # scanning the same item three times counts three of them
    for _ in range(3):
        r = await client.post(f"/api/stocktake/{st['id']}/scan", json={"code": "BC-1"},
                              headers=tenant)
        assert r.status_code == 200, r.text
    assert r.json()["counted_qty"] == 3
    assert r.json()["variance"] == -2         # system says 5, we found 3

    # an unrecognised code is refused rather than silently inventing stock
    bad = await client.post(f"/api/stocktake/{st['id']}/scan", json={"code": "NOPE"},
                            headers=tenant)
    assert bad.status_code == 404

    # unless you explicitly say to register it
    ok = await client.post(f"/api/stocktake/{st['id']}/scan", json={
        "code": "NOPE", "register_unknown": True}, headers=tenant)
    assert ok.status_code == 200 and ok.json()["is_unknown"] is True


@pytest.mark.asyncio
async def test_unknown_product_enters_stock_when_posted(client, tenant, db):
    w = await _wh(client, tenant)
    st = (await client.post("/api/stocktake", json={
        "warehouse_id": w["id"], "kind": "partial"}, headers=tenant)).json()
    r = (await client.post(f"/api/stocktake/{st['id']}/scan", json={
        "code": "FOUND-99", "qty": 4, "register_unknown": True}, headers=tenant)).json()

    post = await client.post(f"/api/stocktake/{st['id']}/post", headers=tenant)
    assert post.status_code == 200, post.text

    sl = (await db.execute(select(StockLevel).where(
        StockLevel.part_id == r["part_id"]))).scalar_one()
    assert sl.qty_available == 4       # found on the shelf → now in the system


@pytest.mark.asyncio
async def test_lines_filter_by_difference_and_summary(client, tenant):
    w = await _wh(client, tenant)
    a = await _part(client, tenant, name="A", sku="A-1", barcode="A-1")
    b = await _part(client, tenant, name="B", sku="B-1", barcode="B-1")
    await _stock(client, tenant, a["id"], 5)
    await _stock(client, tenant, b["id"], 5)
    st = (await client.post("/api/stocktake", json={
        "warehouse_id": w["id"], "kind": "full"}, headers=tenant)).json()

    await client.post(f"/api/stocktake/{st['id']}/scan",
                      json={"code": "A-1", "qty": 7}, headers=tenant)   # surplus
    await client.post(f"/api/stocktake/{st['id']}/scan",
                      json={"code": "B-1", "qty": 2}, headers=tenant)   # shortage

    got = (await client.get(f"/api/stocktake/{st['id']}/lines?diff=surplus",
                            headers=tenant)).json()
    assert [i["part_name"] for i in got["items"]] == ["A"]
    got = (await client.get(f"/api/stocktake/{st['id']}/lines?diff=shortage",
                            headers=tenant)).json()
    assert [i["part_name"] for i in got["items"]] == ["B"]
    assert got["summary"]["surplus"] == 1 and got["summary"]["shortage"] == 1


@pytest.mark.asyncio
async def test_count_import_sets_totals_and_settlement_values_it(client, tenant):
    w = await _wh(client, tenant)
    p = await _part(client, tenant, sku="IMP-1", barcode="IMP-1", cost=5)
    await _stock(client, tenant, p["id"], 10)
    st = (await client.post("/api/stocktake", json={
        "warehouse_id": w["id"], "kind": "full"}, headers=tenant)).json()

    r = await client.post(f"/api/stocktake/{st['id']}/import", json={
        "rows": [{"code": "IMP-1", "counted_qty": 8}, {"code": "GHOST", "counted_qty": 1}],
        "code_field": "code", "qty_field": "counted_qty"}, headers=tenant)
    assert r.status_code == 200, r.text
    assert r.json()["applied"] == 1 and len(r.json()["errors"]) == 1   # GHOST unknown

    act = (await client.get(f"/api/stocktake/{st['id']}/settlement", headers=tenant)).json()
    assert act["lines"][0]["variance"] == -2
    assert act["shortage_value"] == pytest.approx(-10.0)   # 2 missing × 5 cost
    assert act["net_value"] == pytest.approx(-10.0)


@pytest.mark.asyncio
async def test_posted_stocktake_rejects_more_counting(client, tenant):
    w = await _wh(client, tenant)
    p = await _part(client, tenant, barcode="LOCK-1")
    await _stock(client, tenant, p["id"], 1)
    st = (await client.post("/api/stocktake", json={
        "warehouse_id": w["id"], "kind": "full"}, headers=tenant)).json()
    await client.post(f"/api/stocktake/{st['id']}/post", headers=tenant)
    r = await client.post(f"/api/stocktake/{st['id']}/scan", json={"code": "LOCK-1"},
                          headers=tenant)
    assert r.status_code == 409


# ══ Build 92: appointments in the scheduler ══════════════════════════════════════
@pytest.mark.asyncio
async def test_scheduler_day_includes_appointments(client, tenant):
    me = await _me(client, tenant)
    await client.post("/api/appointments", json={
        "client_name": "Jo", "employee_id": me["id"],
        "scheduled_for": "2026-10-01T10:00:00", "duration_min": 60}, headers=tenant)

    day = (await client.get("/api/orders/scheduler/day?date_str=2026-10-01",
                            headers=tenant)).json()
    kinds = {i["kind"] for i in day["items"]}
    assert "appointment" in kinds
    bk = next(i for i in day["items"] if i["kind"] == "appointment")
    assert bk["client_name"] == "Jo" and bk["assigned_to_id"] == me["id"]


@pytest.mark.asyncio
async def test_free_slots_treats_appointments_as_busy(client, tenant):
    me = await _me(client, tenant)
    await client.post("/api/schedules", json={
        "employee_id": me["id"], "day": "2026-10-02", "kind": "shift",
        "start": "09:00", "end": "17:00"}, headers=tenant)
    await client.post("/api/appointments", json={
        "client_name": "Jo", "employee_id": me["id"],
        "scheduled_for": "2026-10-02T10:00:00", "duration_min": 60}, headers=tenant)

    r = (await client.get(
        f"/api/orders/scheduler/free-slots?date_str=2026-10-02&duration_min=60"
        f"&employee_id={me['id']}", headers=tenant)).json()
    starts = {s["start"] for s in r["slots"]} if "slots" in r else set()
    # the 10:00 hour is taken by the appointment, so it must not be offered
    assert "10:00" not in starts


# ══ Build 93: postings as documents ══════════════════════════════════════════
@pytest.mark.asyncio
async def test_posting_sets_stock_avg_cost_and_markup_price(client, tenant, db):
    w = await _wh(client, tenant)
    p = await _part(client, tenant, cost=0, sell=0)

    r = await client.post("/api/stock-docs/postings", json={
        "warehouse_id": w["id"], "reference": "INV-77",
        "lines": [{"part_id": p["id"], "qty": 10, "unit_cost": 4, "markup_pct": 50}]},
        headers=tenant)
    assert r.status_code == 201, r.text
    assert r.json()["total"] == pytest.approx(40.0)

    row = (await db.execute(select(Part).where(Part.id == p["id"]))).scalar_one()
    await db.refresh(row)
    assert row.avg_cost == pytest.approx(4.0)
    assert row.sell_price == pytest.approx(6.0)      # cost 4 + 50% markup
    sl = (await db.execute(select(StockLevel).where(
        StockLevel.part_id == p["id"]))).scalar_one()
    assert sl.qty_available == 10


@pytest.mark.asyncio
async def test_posting_delete_removes_stock_and_supplier_debt(client, tenant, db):
    me = await _me(client, tenant)
    w = await _wh(client, tenant)
    p = await _part(client, tenant)
    sup = Supplier(org_id=me["org_id"], name="Acme", balance=0)
    db.add(sup)
    await db.commit()
    await db.refresh(sup)

    d = (await client.post("/api/stock-docs/postings", json={
        "warehouse_id": w["id"], "supplier_id": sup.id, "settlement": "balance",
        "lines": [{"part_id": p["id"], "qty": 5, "unit_cost": 3}]}, headers=tenant)).json()
    await db.refresh(sup)
    assert sup.balance == pytest.approx(15.0)       # we owe them for the goods

    r = await client.delete(f"/api/stock-docs/postings/{d['id']}", headers=tenant)
    assert r.status_code == 200
    sl = (await db.execute(select(StockLevel).where(
        StockLevel.part_id == p["id"]))).scalar_one()
    await db.refresh(sl)
    await db.refresh(sup)
    assert sl.qty_available == 0 and sup.balance == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_paid_posting_cannot_be_deleted(client, tenant, db):
    w = await _wh(client, tenant)
    p = await _part(client, tenant)
    d = (await client.post("/api/stock-docs/postings", json={
        "warehouse_id": w["id"],
        "lines": [{"part_id": p["id"], "qty": 2, "unit_cost": 1}]}, headers=tenant)).json()
    doc = (await db.execute(select(Posting).where(Posting.id == d["id"]))).scalar_one()
    doc.paid = True
    await db.commit()
    r = await client.delete(f"/api/stock-docs/postings/{d['id']}", headers=tenant)
    assert r.status_code == 409


# ══ Build 93: transfer receive ═══════════════════════════════════════════════
@pytest.mark.asyncio
async def test_transfer_credits_only_what_arrived(client, tenant, db):
    """A box that leaves with 10 and arrives with 8 must not credit 10 at the far end."""
    from backend.models import Location
    me = await _me(client, tenant)
    loc = await _loc(client, tenant)
    dest = Location(org_id=me["org_id"], name="Branch B")
    db.add(dest)
    await db.commit()
    await db.refresh(dest)

    p = await _part(client, tenant, sku="TR-1", barcode="TR-1")
    await _stock(client, tenant, p["id"], 10)

    t = await client.post("/api/transfers", json={
        "from_location_id": loc, "to_location_id": dest.id,
        "lines": [{"part_id": p["id"], "qty": 10}]}, headers=tenant)
    assert t.status_code == 201, t.text
    tid = t.json()["id"]

    for st in ("ready", "in_transit"):
        r = await client.patch(f"/api/transfers/{tid}/status", json={"status": st},
                               headers=tenant)
        assert r.status_code == 200, r.text

    line = (await db.execute(select(StockTransferLine).where(
        StockTransferLine.transfer_id == tid))).scalar_one()

    # you can't receive more than was shipped
    over = await client.post(f"/api/transfers/{tid}/receive", json={
        "lines": [{"line_id": line.id, "qty_received": 12}]}, headers=tenant)
    assert over.status_code == 409

    rec = await client.post(f"/api/transfers/{tid}/receive", json={
        "lines": [{"line_id": line.id, "qty_received": 8}]}, headers=tenant)
    assert rec.status_code == 200 and rec.json()["discrepancies"] == 1
    assert rec.json()["status"] == "received"

    done = await client.patch(f"/api/transfers/{tid}/status", json={"status": "completed"},
                              headers=tenant)
    assert done.status_code == 200, done.text

    dest_sl = (await db.execute(select(StockLevel).where(
        StockLevel.part_id == p["id"], StockLevel.location_id == dest.id))).scalar_one()
    assert dest_sl.qty_available == 8        # only what actually turned up


@pytest.mark.asyncio
async def test_transfer_scan_adds_lines(client, tenant, db):
    from backend.models import Location
    me = await _me(client, tenant)
    loc = await _loc(client, tenant)
    dest = Location(org_id=me["org_id"], name="Branch C")
    db.add(dest)
    await db.commit()
    await db.refresh(dest)

    p = await _part(client, tenant, sku="SC-1", barcode="SC-1")
    await _stock(client, tenant, p["id"], 5)
    t = await client.post("/api/transfers", json={
        "from_location_id": loc, "to_location_id": dest.id,
        "lines": [{"part_id": p["id"], "qty": 1}]}, headers=tenant)
    assert t.status_code == 201, t.text
    t = t.json()

    # scanning the same product twice more brings the line to 3
    for _ in range(2):
        r = await client.post(f"/api/transfers/{t['id']}/scan", json={"code": "SC-1"},
                              headers=tenant)
        assert r.status_code == 200, r.text
    assert r.json()["qty"] == 3      # scans accumulate onto the existing line

    miss = await client.post(f"/api/transfers/{t['id']}/scan", json={"code": "ZZZ"},
                             headers=tenant)
    assert miss.status_code == 404


# ══ Build 94: suppliers + PO reservations ════════════════════════════════════
@pytest.mark.asyncio
async def test_supplier_crud_and_delete_guard(client, tenant, db):
    s = await client.post("/api/suppliers", json={
        "name": "Acme Parts", "email": "a@acme.dev", "lead_days": 3}, headers=tenant)
    assert s.status_code == 201, s.text
    sid = s.json()["id"]

    u = await client.put(f"/api/suppliers/{sid}", json={"name": "Acme Ltd"}, headers=tenant)
    assert u.status_code == 200 and u.json()["name"] == "Acme Ltd"

    row = (await db.execute(select(Supplier).where(Supplier.id == sid))).scalar_one()
    row.balance = 25
    await db.commit()
    blocked = await client.delete(f"/api/suppliers/{sid}", headers=tenant)
    assert blocked.status_code == 409          # a debt is a real obligation

    row.balance = 0
    await db.commit()
    assert (await client.delete(f"/api/suppliers/{sid}", headers=tenant)).status_code == 200


@pytest.mark.asyncio
async def test_price_list_import_updates_costs(client, tenant, db):
    s = (await client.post("/api/suppliers", json={"name": "S"}, headers=tenant)).json()
    p = await _part(client, tenant, sku="PL-1", cost=2, sell=4)

    dry = await client.post(f"/api/suppliers/{s['id']}/import-price-list", json={
        "rows": [{"Code": "PL-1", "Cost": "3.50"}],
        "mapping": {"Code": "sku", "Cost": "cost_price"}, "dry_run": True}, headers=tenant)
    assert dry.status_code == 200 and dry.json()["updated"] == 1
    row = (await db.execute(select(Part).where(Part.id == p["id"]))).scalar_one()
    await db.refresh(row)
    assert row.cost_price == pytest.approx(2.0)      # preview changed nothing

    real = await client.post(f"/api/suppliers/{s['id']}/import-price-list", json={
        "rows": [{"Code": "PL-1", "Cost": "3.50"}],
        "mapping": {"Code": "sku", "Cost": "cost_price"}}, headers=tenant)
    assert real.json()["updated"] == 1
    await db.refresh(row)
    assert row.cost_price == pytest.approx(3.5)

    # a mapping with no match key is refused — it could rewrite the whole catalogue
    bad = await client.post(f"/api/suppliers/{s['id']}/import-price-list", json={
        "rows": [], "mapping": {"Cost": "cost_price"}}, headers=tenant)
    assert bad.status_code == 422


@pytest.mark.asyncio
async def test_po_reservation_survives_into_stock(client, tenant, db):
    """Goods promised to a customer must arrive already spoken for."""
    me = await _me(client, tenant)
    loc = await _loc(client, tenant)
    p = await _part(client, tenant, sku="PO-1")
    o = (await client.post("/api/orders", json={
        "location_id": loc, "complaint": "waiting on part"}, headers=tenant)).json()

    sup = Supplier(org_id=me["org_id"], name="PO supplier", balance=0)
    db.add(sup)
    await db.flush()
    po = PurchaseOrder(supplier_id=sup.id, location_id=loc, po_number="PO-1",
                       status="ordered", expected_at=datetime.utcnow() + timedelta(days=3))
    db.add(po)
    await db.flush()
    ln = POLine(po_id=po.id, part_id=p["id"], qty_ordered=2)
    db.add(ln)
    await db.commit()
    await db.refresh(ln)

    r = await client.post(
        f"/api/suppliers/purchase-orders/{po.id}/lines/{ln.id}/reserve",
        json={"order_id": o["id"]}, headers=tenant)
    assert r.status_code == 200, r.text
    assert r.json()["reserved_for"] == o["order_number"]

    inc = (await client.get("/api/suppliers/purchase-orders/incoming",
                            headers=tenant)).json()
    mine = next(i for i in inc["items"] if i["line_id"] == ln.id)
    assert mine["qty_incoming"] == 2 and mine["reserved_for"] == o["order_number"]
    assert mine["delayed"] is False

    # Receive through the single real path — it posts stock AND honours the reservation.
    rec = await client.post(f"/api/inventory/po/{po.id}/receive", json={}, headers=tenant)
    assert rec.status_code == 200, rec.text

    res = (await db.execute(select(StockReservation).where(
        StockReservation.order_id == o["id"]))).scalar_one()
    assert res.qty == 2 and res.released is False
    # …and the goods actually landed in stock, spoken for (not free).
    sl = (await db.execute(select(StockLevel).where(
        StockLevel.part_id == p["id"], StockLevel.location_id == loc))).scalar_one()
    assert sl.qty_available == 2 and sl.qty_reserved == 2


@pytest.mark.asyncio
async def test_overdue_po_is_reported_as_delayed(client, tenant, db):
    me = await _me(client, tenant)
    loc = await _loc(client, tenant)
    p = await _part(client, tenant, sku="PO-2")
    sup = Supplier(org_id=me["org_id"], name="Late supplier", balance=0)
    db.add(sup)
    await db.flush()
    po = PurchaseOrder(supplier_id=sup.id, location_id=loc, po_number="PO-2",
                       status="ordered", expected_at=datetime.utcnow() - timedelta(days=2))
    db.add(po)
    await db.flush()
    db.add(POLine(po_id=po.id, part_id=p["id"], qty_ordered=1))
    await db.commit()

    inc = (await client.get("/api/suppliers/purchase-orders/incoming",
                            headers=tenant)).json()
    assert any(i["delayed"] for i in inc["items"])   # the date quietly went by


# ══ Build 95: printing ═══════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_label_template_crud_and_validation(client, tenant):
    t = await client.post("/api/print/templates", json={
        "name": "Shelf tag", "kind": "price_tag", "width_mm": 60, "height_mm": 40,
        "fields": ["name", "price", "barcode"], "barcode_type": "qr",
        "is_default": True}, headers=tenant)
    assert t.status_code == 201, t.text
    assert t.json()["barcode_type"] == "qr"

    lst = (await client.get("/api/print/templates", headers=tenant)).json()
    assert any(x["id"] == t.json()["id"] for x in lst["items"])
    assert "barcode" in lst["fields"]

    tiny = await client.post("/api/print/templates", json={
        "name": "Too small", "width_mm": 10, "height_mm": 5}, headers=tenant)
    assert tiny.status_code == 422        # a barcode wouldn't scan at that size

    bad = await client.post("/api/print/templates", json={
        "name": "Bad", "fields": ["nonsense"]}, headers=tenant)
    assert bad.status_code == 422


@pytest.mark.asyncio
async def test_print_documents_render(client, tenant):
    w = await _wh(client, tenant)
    p = await _part(client, tenant)
    await _stock(client, tenant, p["id"], 10)

    post = (await client.post("/api/stock-docs/postings", json={
        "warehouse_id": w["id"],
        "lines": [{"part_id": p["id"], "qty": 2, "unit_cost": 3}]}, headers=tenant)).json()
    wo = (await client.post("/api/stock-docs/write-offs", json={
        "warehouse_id": w["id"], "reason": "damaged",
        "lines": [{"part_id": p["id"], "qty": 1, "comment": "internal only"}]},
        headers=tenant)).json()
    st = (await client.post("/api/stocktake", json={
        "warehouse_id": w["id"], "kind": "full"}, headers=tenant)).json()

    for url in (f"/api/print/postings/{post['id']}",
                f"/api/print/write-offs/{wo['id']}",
                f"/api/print/stocktakes/{st['id']}"):
        r = await client.get(url, headers=tenant)
        assert r.status_code == 200, f"{url} → {r.status_code}"
        assert r.content[:5] == b"%PDF-", url


@pytest.mark.asyncio
async def test_private_write_off_comment_stays_off_the_printout(client, tenant):
    """A comment marked internal must not appear on a document that leaves the shop."""
    w = await _wh(client, tenant)
    p = await _part(client, tenant)
    await _stock(client, tenant, p["id"], 5)
    wo = (await client.post("/api/stock-docs/write-offs", json={
        "warehouse_id": w["id"],
        "lines": [{"part_id": p["id"], "qty": 1,
                   "comment": "SECRETNOTE", "comment_public": False}]},
        headers=tenant)).json()

    r = await client.get(f"/api/print/write-offs/{wo['id']}", headers=tenant)
    assert r.status_code == 200
    # reportlab compresses streams, so decode the text out of the PDF to be sure
    import zlib
    raw = r.content
    found = b"SECRETNOTE" in raw
    for chunk in raw.split(b"stream")[1:]:
        body = chunk.split(b"endstream")[0].strip(b"\r\n")
        try:
            if b"SECRETNOTE" in zlib.decompress(body):
                found = True
        except zlib.error:
            continue
    assert not found
