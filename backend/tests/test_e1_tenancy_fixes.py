"""Re-review E1 (tenancy N2, N5, N6, N8; audit SEC-11, SEC-13).

- N2: a checklist template holding a stale `upsell_ref_id` (a catalog row deleted, or junk saved
  before SEC-08) 422'd every job it attached to (by hand, on creation and on a status change), and
  the template couldn't be re-saved without editing that item. Importer undo hard-deleted parts a
  soft reference still pointed at.
- N5: `POST /api/assets` let any role create company inventory assets at any location.
- N6: a payments custom report with a location filter was a 500 (`Payment.location_id`).
- N8: the org_id fill hook walked every loaded object on every flush (O(n²) bulk imports).
- SEC-11: `role_allows(None, …)` was True.
- SEC-13: `order_statuses` reads were not tenant-filtered."""
import uuid

import pytest
from sqlalchemy import select, update

from backend.tests import kit_helpers as k

pytestmark = pytest.mark.asyncio

_BODY = lambda svc, keys=None: {"name": f"Intake {uuid.uuid4().hex[:4]}", "scope": "all",  # noqa: E731
                                **({"auto_status_keys": keys} if keys is not None else {}),
                                "steps": [{"title": "S", "sections": [{"title": "Sec", "items": [
                                    {"label": "Battery", "upsell_ref_id": svc, "upsell_kind": "service"},
                                    {"label": "Screen"}]}]}]}


async def _stale_template(client, h, db, keys=None):
    from backend.models import ChecklistTemplateItem
    svc = await k.service(client, h, "Clean", 10)
    r = await client.post("/api/checklist-templates", json=_BODY(svc, keys), headers=h)
    assert r.status_code == 201, r.text
    tid = r.json()["id"]
    await db.execute(update(ChecklistTemplateItem).where(
        ChecklistTemplateItem.template_id == tid, ChecklistTemplateItem.label == "Battery")
        .values(upsell_ref_id="gone-legacy-id"))
    await db.commit()
    return tid


# ── N2 ─────────────────────────────────────────────────────────────────────────────────────────
async def test_stale_upsell_ref_doesnt_block_attaching_the_checklist(client, tenant, db):
    from backend.models import OrderChecklistItem
    tid = await _stale_template(client, tenant, db)
    loc = await k.loc(client, tenant)
    oid, _ = await k.job(client, tenant, loc)
    r = await client.post(f"/api/orders/{oid}/checklists", json={"template_id": tid}, headers=tenant)
    assert r.status_code == 201, r.text                     # was 422
    db.expire_all()
    items = (await db.execute(select(OrderChecklistItem).where(OrderChecklistItem.order_id == oid))).scalars().all()
    assert {i.label: i.upsell_ref_id for i in items} == {"Battery": None, "Screen": None}


async def test_stale_upsell_ref_doesnt_block_creating_a_job(client, tenant, db):
    from backend.models import OrderChecklist
    loc = await k.loc(client, tenant)
    first, _ = await k.job(client, tenant, loc)
    start = (await k.order(client, tenant, first))["status"]
    tid = await _stale_template(client, tenant, db, keys=[{"record_type": "work_order", "key": start}])
    oid, _ = await k.job(client, tenant, loc)               # k.job asserts 200/201 (was 422)
    db.expire_all()
    attached = (await db.execute(select(OrderChecklist.template_id).where(
        OrderChecklist.order_id == oid))).scalars().all()
    assert attached == [tid]


async def test_stale_upsell_ref_doesnt_block_moving_a_job_into_an_auto_attach_status(client, tenant, db):
    from backend.models import OrderChecklist
    tid = await _stale_template(client, tenant, db, keys=[{"record_type": "work_order", "key": "in_repair"}])
    loc = await k.loc(client, tenant)
    oid, _ = await k.job(client, tenant, loc)
    r = await client.patch(f"/api/orders/{oid}/status", json={"status": "in_repair"}, headers=tenant)
    assert r.status_code == 200, r.text                     # was 422
    db.expire_all()
    attached = (await db.execute(select(OrderChecklist.template_id).where(
        OrderChecklist.order_id == oid))).scalars().all()
    assert attached == [tid]


async def test_template_with_a_stale_upsell_ref_can_be_resaved(client, tenant, db):
    from backend.models import ChecklistTemplateItem
    tid = await _stale_template(client, tenant, db)
    tpl = (await client.get(f"/api/checklist-templates/{tid}", headers=tenant)).json()
    body = {"name": "Intake renamed", "steps": [{"title": s["title"], "sections": [
        {"title": sec["title"], "items": [{f: it[f] for f in ("label", "upsell_ref_id", "upsell_kind")}
                                          for it in sec["items"]]} for sec in s["sections"]]} for s in tpl["steps"]]}
    r = await client.put(f"/api/checklist-templates/{tid}", json=body, headers=tenant)
    assert r.status_code == 200, r.text                     # was 422
    db.expire_all()
    refs = (await db.execute(select(ChecklistTemplateItem.upsell_ref_id).where(
        ChecklistTemplateItem.template_id == tid))).scalars().all()
    assert refs == [None, None]
    # a NEW unknown id is still refused (SEC-08 stays)
    body["steps"][0]["sections"][0]["items"][0]["upsell_ref_id"] = "another-made-up-id"
    r = await client.put(f"/api/checklist-templates/{tid}", json=body, headers=tenant)
    assert r.status_code == 422, r.text


async def test_import_undo_keeps_a_part_a_soft_reference_points_at(client, tenant, db):
    from backend.models import Part
    from backend.tests.test_import_centre import _import, _me
    me = await _me(client, tenant)
    out = await _import(client, tenant, "products", [["Name", "SKU", "Sell price"], ["Glass", "GL-1", "9"]])
    pid = (await db.execute(select(Part.id).where(Part.org_id == me["org_id"], Part.sku == "GL-1"))).scalar_one()
    r = await client.post("/api/checklist-templates", headers=tenant, json={
        "name": "Upsell glass", "steps": [{"title": "S", "sections": [{"title": "", "items": [
            {"label": "Glass ok", "upsell_ref_id": pid, "upsell_kind": "product"}]}]}]})
    assert r.status_code == 201, r.text
    r = await client.post(f"/api/imports/batches/{out['batch_id']}/undo", headers=tenant)
    assert r.status_code == 409, r.text                     # was 200: the template now pointed at nothing
    db.expire_all()
    assert (await db.execute(select(Part.id).where(Part.id == pid))).scalar_one_or_none() == pid


# ── N5 ─────────────────────────────────────────────────────────────────────────────────────────
async def test_company_asset_needs_inventory_rights_and_location_reach(client, tenant, db):
    from backend.tests.test_qa_f1_jobs import _staff
    loc = await k.loc(client, tenant)
    tech = await _staff(client, tenant, {"can_manage_inventory": False})
    r = await client.post("/api/assets", headers=tech, json={
        "owner_type": "company", "asset_type": "Device", "brand": "Apple", "model": "Loaner", "location_id": loc})
    assert r.status_code == 403, r.text                     # was 201
    cid = (await client.post("/api/clients", json={"name": "Ann"}, headers=tenant)).json()["id"]
    r = await client.post("/api/assets", headers=tech, json={
        "owner_type": "client", "client_id": cid, "asset_type": "Device", "brand": "Apple", "model": "13"})
    assert r.status_code == 201, r.text                     # client intake stays open to staff
    mgr = await _staff(client, tenant, {"can_manage_inventory": True})
    r = await client.post("/api/assets", headers=mgr, json={
        "owner_type": "company", "asset_type": "Device", "brand": "Apple", "model": "Loaner", "location_id": loc})
    assert r.status_code == 201, r.text


async def test_company_asset_at_a_location_out_of_scope_is_refused(client, tenant, db):
    from backend.models import Location
    from backend.tests.test_e1_inventory_reach import _login_with
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    main = await k.loc(client, tenant)
    b = Location(org_id=me["org_id"], name="Branch B")
    db.add(b)
    await db.commit()
    await db.refresh(b)
    H = await _login_with(client, db, me["org_id"], f"as-{uuid.uuid4().hex[:6]}@x.dev",
                          {"can_manage_inventory": True}, location_scope="selected", location_ids=f'["{b.id}"]')
    r = await client.post("/api/assets", headers=H, json={
        "owner_type": "company", "asset_type": "Device", "brand": "Apple", "model": "Loaner", "location_id": main})
    assert r.status_code == 403, r.text


# ── N6 ─────────────────────────────────────────────────────────────────────────────────────────
async def test_payments_custom_report_filters_by_the_jobs_branch(client, tenant, db):
    from backend.models import Location, Payment
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    main = await k.loc(client, tenant)
    b = Location(org_id=me["org_id"], name="Branch B")
    db.add(b)
    await db.commit()
    await db.refresh(b)
    o_main, _ = await k.job(client, tenant, main)
    o_b, _ = await k.job(client, tenant, b.id)
    for oid, amt in ((o_main, 11), (o_b, 22)):
        db.add(Payment(org_id=me["org_id"], order_id=oid, amount=amt, method="cash"))
    await db.commit()
    cfg = {"source": "payments", "aggregates": [{"fn": "count"}]}
    r = await client.post("/api/custom-reports/run", json={"config": {"source": "payments"}, "location_id": b.id},
                          headers=tenant)
    assert r.status_code == 200, r.text                     # was 500
    cols = [c["key"] for c in r.json()["columns"]]
    amounts = sorted(float(row[cols.index("amount")] or 0) for row in r.json()["rows"])
    assert amounts == [22.0], r.json()
    r = await client.post("/api/custom-reports/run", json={"config": cfg}, headers=tenant)
    assert r.status_code == 200, r.text


# ── N8 ─────────────────────────────────────────────────────────────────────────────────────────
async def test_org_fill_hook_doesnt_walk_every_loaded_row(client, tenant, db, monkeypatch):
    """A big session (bulk import) autoflushing one new child row at a time: the hook must look at
    the new rows, not at the thousands already loaded."""
    from backend.db import tenancy
    from backend.models import Client, OrderComment
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    loc = await k.loc(client, tenant)
    oid, _ = await k.job(client, tenant, loc)
    for i in range(300):
        db.add(Client(org_id=me["org_id"], name=f"Bulk {i}"))
    await db.commit()
    loaded = (await db.execute(select(Client).where(Client.org_id == me["org_id"]))).scalars().all()
    assert len(loaded) >= 300
    seen = []
    real = tenancy._child_collections
    monkeypatch.setattr(tenancy, "_child_collections", lambda m: seen.append(m) or real(m))
    db.add(OrderComment(order_id=oid, body="hi"))
    await db.flush()
    assert len(seen) < 20, len(seen)                        # was one per loaded row (300+)
    c = (await db.execute(select(OrderComment).where(OrderComment.order_id == oid))).scalars().first()
    assert c.org_id == me["org_id"]
    await db.rollback()


async def test_org_fill_hook_still_reads_a_parent_from_its_collection(db, client, tenant):
    """The child appended to a loaded parent's collection (no backref) still takes its shop."""
    from backend.models import Invoice, InvoiceLine
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    inv = Invoice(org_id=me["org_id"], invoice_number=f"T-{uuid.uuid4().hex[:5]}")
    db.add(inv)
    await db.commit()
    inv = (await db.execute(select(Invoice).where(Invoice.id == inv.id))).scalar_one()
    await db.refresh(inv, attribute_names=["lines"])
    line = InvoiceLine(description="x", qty=1, unit_price=1)
    inv.lines.append(line)
    await db.flush()
    assert line.org_id == me["org_id"]
    await db.rollback()


# ── SEC-11 ─────────────────────────────────────────────────────────────────────────────────────
async def test_role_allows_none_is_false():
    from backend.services.permissions import role_allows
    assert role_allows(None, "can_view_finance") is False


async def test_close_shift_with_a_dangling_role_hides_expected_cash(client, tenant, db):
    """A role id that no longer loads must not read as the owner (finance close_shift blind count)."""
    from backend.services.permissions import effective_permissions
    assert not any(effective_permissions(None).values())


# ── SEC-13 ─────────────────────────────────────────────────────────────────────────────────────
async def test_order_status_reads_are_tenant_filtered(client, tenant, db):
    from backend.db.tenancy import set_tenant
    from backend.models import OrderStatus
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    email = f"o2-{uuid.uuid4().hex[:6]}@t.dev"
    r = await client.post("/api/auth/register", json={"org_name": "Other", "name": "B", "email": email,
                                                      "password": "pw-test-12345"})
    other = {"Authorization": f"Bearer {r.json()['access_token']}"}
    other_org = (await client.get("/api/auth/me", headers=other)).json()["org_id"]
    theirs = (await db.execute(select(OrderStatus.id).where(OrderStatus.org_id == other_org))).scalars().first()
    assert theirs
    db.add(OrderStatus(org_id=None, key=f"plat_{uuid.uuid4().hex[:4]}", name="Platform default"))
    await db.commit()
    db.expire_all()
    db.sync_session.info.pop("tenant_org", None)
    set_tenant(db, me["org_id"])
    try:
        assert (await db.execute(select(OrderStatus).where(OrderStatus.id == theirs))).scalar_one_or_none() is None
        orgs = set((await db.execute(select(OrderStatus.org_id))).scalars().all())
        assert orgs <= {me["org_id"], None} and None in orgs and me["org_id"] in orgs
    finally:
        db.sync_session.info.pop("tenant_org", None)
