"""Statuses page: each status shows how many jobs use it, and deleting one can move its jobs."""
import pytest


async def _new_order(client, h):
    loc = (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]
    cid = (await client.post("/api/clients", json={"name": "C"}, headers=h)).json()["id"]
    return (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x"},
                              headers=h)).json()


async def _by_key(client, h):
    return {s["key"]: s for s in (await client.get("/api/statuses", headers=h)).json()["items"]}


@pytest.mark.asyncio
async def test_job_counts(client, tenant):
    a = await _new_order(client, tenant)
    b = await _new_order(client, tenant)
    await client.patch(f"/api/orders/{b['id']}/status", json={"status": "waiting_parts"}, headers=tenant)
    c = await _new_order(client, tenant)
    await client.patch(f"/api/orders/{c['id']}/status", json={"status": "done"}, headers=tenant)

    st = await _by_key(client, tenant)
    assert st["new"]["job_count"] == 1 and st["new"]["in_use"] == 1
    assert st["waiting_parts"]["job_count"] == 1
    assert st["done"]["job_count"] == 1                # closed within 30 days
    assert st["in_repair"]["job_count"] == 0
    assert a["id"]
    # other pipelines carry no counts
    inq = (await client.get("/api/statuses?record_type=inquiry", headers=tenant)).json()["items"]
    assert all("job_count" not in s for s in inq)


@pytest.mark.asyncio
async def test_delete_moves_jobs_and_logs(client, tenant):
    o = await _new_order(client, tenant)
    await client.patch(f"/api/orders/{o['id']}/status", json={"status": "waiting_parts"}, headers=tenant)
    st = await _by_key(client, tenant)

    bad = await client.delete(f"/api/statuses/{st['waiting_parts']['id']}?move_to=nope", headers=tenant)
    assert bad.status_code == 400
    self_move = await client.delete(f"/api/statuses/{st['waiting_parts']['id']}?move_to=waiting_parts", headers=tenant)
    assert self_move.status_code == 400

    r = await client.delete(f"/api/statuses/{st['waiting_parts']['id']}?move_to=in_repair", headers=tenant)
    assert r.status_code == 200, r.text
    d = (await client.get(f"/api/orders/{o['id']}", headers=tenant)).json()
    assert d["status"] == "in_repair"
    st2 = await _by_key(client, tenant)
    assert "waiting_parts" not in st2 and st2["in_repair"]["in_use"] == 1


@pytest.mark.asyncio
async def test_delete_without_move_still_works(client, tenant):
    st = await _by_key(client, tenant)
    r = await client.delete(f"/api/statuses/{st['diagnosed']['id']}", headers=tenant)
    assert r.status_code == 200
