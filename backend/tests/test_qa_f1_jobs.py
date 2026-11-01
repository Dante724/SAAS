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

