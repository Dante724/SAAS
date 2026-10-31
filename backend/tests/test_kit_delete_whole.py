"""Removing a whole kit from a job is one step: all its lines go (stock back), or none do."""
import uuid

import pytest

from backend.tests import kit_helpers as k
from backend.tests.test_kit_print_one_line import _job_with_kit_and_charger, _shop


@pytest.mark.asyncio
async def test_remove_whole_kit_returns_stock_and_keeps_other_lines(client, tenant):
    s = await _shop(client, tenant)
    oid, _cid, gid = await _job_with_kit_and_charger(client, tenant, s)
    before = await k.lines(client, tenant, oid)
    others = [ln for ln in before if ln["bundle_group_id"] != gid]
    r = await client.delete(f"/api/orders/{oid}/kits/{gid}", headers=tenant)
    assert r.status_code == 200, r.text
    assert r.json()["removed"] == len(before) - len(others)
    after = await k.lines(client, tenant, oid)
    assert [ln["id"] for ln in after] == [ln["id"] for ln in others]          # only the kit went
    assert await k.stock(client, tenant, s.screen) == 10                       # its parts came back
    assert await k.stock(client, tenant, s.glue) == 10
    assert (await k.order(client, tenant, oid))["total"] == round(sum(ln["total"] for ln in others), 2)
    # a kit that isn't on the job
    assert (await client.delete(f"/api/orders/{oid}/kits/{uuid.uuid4()}", headers=tenant)).status_code == 404


@pytest.mark.asyncio
async def test_invoiced_kit_is_not_half_removed(client, tenant):
    s = await _shop(client, tenant)
    oid, _cid, gid = await _job_with_kit_and_charger(client, tenant, s)
    scr = [ln for ln in await k.lines(client, tenant, oid) if ln["ref_id"] == s.screen][0]
    r = await client.post(f"/api/cash/orders/{oid}/invoices", headers=tenant, json={"line_ids": [scr["id"]]})
    assert r.status_code == 201
    before = await k.lines(client, tenant, oid)
    r = await client.delete(f"/api/orders/{oid}/kits/{gid}", headers=tenant)
    assert r.status_code == 409 and "invoice" in r.text
    assert [ln["id"] for ln in await k.lines(client, tenant, oid)] == [ln["id"] for ln in before]
