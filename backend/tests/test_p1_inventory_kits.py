"""Audit P1 inventory, kits (bundles):

- INV-09: a kit component's quantity is validated: positive, sane, and whole for a product counted
  in whole units. An archived kit can't be added to a job. A kit already stored with a bad quantity
  is refused with a message, not a 500.
- INV-10: removing a whole kit from a job takes the job's lock, re-reads the lines and audits each
  line it removes, like removing a single line.
"""
import pytest
from sqlalchemy import select

from backend.tests import kit_helpers as K
from backend.tests.test_kit_print_one_line import _job_with_kit_and_charger, _shop
from backend.tests.test_line_edit_audit import _audit

pytestmark = pytest.mark.asyncio


async def _new_kit(client, h, comps, name="Kit Q"):
    return await client.post("/api/bundles", headers=h, json={"name": name, "components": comps})


async def test_kit_component_qty_must_be_positive_and_whole(client, tenant):
    loc = await K.loc(client, tenant)
    pid = await K.part(client, tenant, loc, "Screw", 1, 0.1, qty=0)
    sid = await K.service(client, tenant, "Fitting", 30)
    for bad in (0, -1, 1.5, 1e9):
        r = await _new_kit(client, tenant, [{"ref_type": "product", "ref_id": pid, "qty": bad}])
        assert r.status_code in (400, 422), (bad, r.text)                 # was 201
    r = await _new_kit(client, tenant, [{"ref_type": "service", "ref_id": sid, "qty": 1.5},
                                        {"ref_type": "product", "ref_id": pid, "qty": 2}])
    assert r.status_code == 201, r.text                                     # half an hour of labour is fine
    kit = r.json()
    r = await client.put(f"/api/bundles/{kit['id']}", headers=tenant, json={
        "name": kit["name"], "components": [{"ref_type": "product", "ref_id": pid, "qty": 0}]})
    assert r.status_code in (400, 422), r.text


async def test_archived_or_broken_kit_cant_be_added_to_a_job(client, tenant, db):
    from backend.models import BundleComponent
    loc = await K.loc(client, tenant)
    pid = await K.part(client, tenant, loc, "Glass", 10, 2, qty=5)
    kit = await K.kit(client, tenant, [{"ref_type": "product", "ref_id": pid, "qty": 1}], name="Glass kit")
    oid, _ = await K.job(client, tenant, loc)
    body = {"kind": "bundle", "line_type": "part", "description": "kit", "ref_id": kit["id"], "qty": 1}

    # a kit stored before validation existed, with a zero quantity: a clear refusal, not a 500
    comp = (await db.execute(select(BundleComponent).where(BundleComponent.bundle_id == kit["id"]))).scalar_one()
    comp.qty = 0
    await db.commit()
    r = await client.post(f"/api/orders/{oid}/lines", headers=tenant, json=body)
    assert r.status_code == 409, r.text
    assert await K.stock(client, tenant, pid) == 5

    assert (await client.delete(f"/api/bundles/{kit['id']}", headers=tenant)).status_code == 200
    r = await client.post(f"/api/orders/{oid}/lines", headers=tenant, json=body)
    assert r.status_code == 404, r.text                                      # was 201 for an archived kit
    assert await K.stock(client, tenant, pid) == 5


async def test_removing_a_whole_kit_is_audited_per_line(client, tenant):
    s = await _shop(client, tenant)
    oid, _cid, gid = await _job_with_kit_and_charger(client, tenant, s)
    kit_lines = [ln for ln in await K.lines(client, tenant, oid) if ln["bundle_group_id"] == gid]
    r = await client.delete(f"/api/orders/{oid}/kits/{gid}", headers=tenant)
    assert r.status_code == 200, r.text
    deleted = {a["diff"]["line_id"] for a in await _audit(client, tenant, oid) if a["action"] == "order.line_delete"}
    assert deleted == {ln["id"] for ln in kit_lines}                         # was: no audit at all
    # removing it again finds nothing to remove and gives nothing back twice
    assert (await client.delete(f"/api/orders/{oid}/kits/{gid}", headers=tenant)).status_code == 404
    assert await K.stock(client, tenant, s.screen) == 10
