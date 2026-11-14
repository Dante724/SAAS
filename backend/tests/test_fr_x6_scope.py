"""Final-review fix wave X6: record and location scope on the side doors.

- SEC-05: a technician limited to their own jobs saw colleagues' jobs (number, status, total,
  line prices) through the client's Documents and Purchases tabs and the asset's history, while
  opening the job itself returned 403.
- SEC-06: counter sales ignored the role's location limit: a Branch B user listed, opened, voided
  and returned Branch A's sales.
"""
import uuid

import pytest
from sqlalchemy import select

from backend.models import Sale


async def _register(client):
    email = f"x6-{uuid.uuid4().hex[:8]}@t.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Co", "name": "Owner", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _as_role(client, H, **role):
    r = await client.post("/api/settings/rbac/roles", json={
        "name": f"R-{uuid.uuid4().hex[:4]}", "template": "Full access", **role}, headers=H)
    assert r.status_code in (200, 201), r.text
    email = f"tm-{uuid.uuid4().hex[:8]}@t.dev"
    r = await client.post("/api/staff/employees", json={
        "name": "Tm", "email": email, "password": "pw-test-12345", "role_id": r.json()["id"]}, headers=H)
    assert r.status_code == 201, r.text
    tok = await client.post("/api/auth/token", data={"username": email, "password": "pw-test-12345"})
    return {"Authorization": f"Bearer {tok.json()['access_token']}"}


_LINE = {"line_type": "labour", "kind": "service", "description": "Fix", "qty": 1, "unit_price": 100}


@pytest.mark.asyncio
async def test_client_tabs_and_asset_history_follow_own_jobs_scope(client):
    H = await _register(client)
    T = await _as_role(client, H, order_scope="own")
    me = (await client.get("/api/auth/me", headers=H)).json()["id"]
    cid = (await client.post("/api/clients", json={"name": "Ravi"}, headers=H)).json()["id"]
    r = await client.post("/api/assets", json={"owner_type": "client", "client_id": cid,
                                               "brand": "Apple", "model": "iPhone"}, headers=H)
    assert r.status_code in (200, 201), r.text
    did = r.json()["id"]
    theirs = (await client.post("/api/orders", json={"client_id": cid, "device_id": did,
                                                     "lines": [_LINE]}, headers=H)).json()["id"]
    pool = (await client.post("/api/orders", json={"client_id": cid, "device_id": did,
                                                   "lines": [_LINE]}, headers=H)).json()["id"]
    r = await client.post("/api/orders/bulk", json={"order_ids": [theirs], "action": "assign", "value": me},
                          headers=H)
    assert r.status_code == 200, r.text
    assert (await client.get(f"/api/orders/{theirs}", headers=T)).status_code == 403

    docs = (await client.get(f"/api/clients/{cid}/documents", headers=T)).json()["items"]
    assert [d["id"] for d in docs] == [pool]
    buys = (await client.get(f"/api/clients/{cid}/purchases", headers=T)).json()
    assert {i["source_id"] for i in buys["items"]} == {pool}
    assert buys["summary"]["total_spent"] == pytest.approx(sum(i["total"] for i in buys["items"]))
    asset = (await client.get(f"/api/assets/{did}", headers=T)).json()
    assert [h["id"] for h in asset["history"]] == [pool]

    # the owner still sees both
    assert len((await client.get(f"/api/clients/{cid}/documents", headers=H)).json()["items"]) == 2
    assert {i["source_id"] for i in (await client.get(f"/api/clients/{cid}/purchases", headers=H)).json()["items"]} \
        == {theirs, pool}
    assert len((await client.get(f"/api/assets/{did}", headers=H)).json()["history"]) == 2


@pytest.mark.asyncio
async def test_counter_sales_stay_inside_a_roles_locations(client, db):
    H = await _register(client)
    r = await client.post("/api/sales", json={"lines": [{
        "kind": "service", "ref_id": None, "description": "Diagnostic", "qty": 1, "unit_price": 40}]}, headers=H)
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    branch_a = (await db.execute(select(Sale.location_id).where(Sale.id == sid))).scalar_one()
    branch_b = (await client.post("/api/staff/locations", json={"name": "Branch B"}, headers=H)).json()["id"]
    assert branch_b != branch_a
    B = await _as_role(client, H, location_scope="selected", location_ids=[branch_b])
    A = await _as_role(client, H, location_scope="selected", location_ids=[branch_a])

    assert sid not in [s["id"] for s in (await client.get("/api/sales", headers=B)).json()["items"]]
    assert sid not in [s["id"] for s in (await client.get(f"/api/sales?location_id={branch_a}",
                                                          headers=B)).json()["items"]]
    assert (await client.get(f"/api/sales/{sid}", headers=B)).status_code == 403
    assert (await client.get(f"/api/sales/{sid}/receipt", headers=B)).status_code == 403
    line = (await client.get(f"/api/sales/{sid}", headers=H)).json()["lines"][0]["id"]
    r = await client.post(f"/api/sales/{sid}/returns", json={"lines": [{"line_id": line, "qty": 1}]}, headers=B)
    assert r.status_code == 403, r.text
    assert (await client.delete(f"/api/sales/{sid}", headers=B)).status_code == 403
    assert (await db.execute(select(Sale.voided_at).where(Sale.id == sid))).scalar_one() is None

    # Branch A's own staff and the owner still work with it
    assert sid in [s["id"] for s in (await client.get("/api/sales", headers=A)).json()["items"]]
    assert (await client.get(f"/api/sales/{sid}", headers=A)).status_code == 200
    assert sid in [s["id"] for s in (await client.get("/api/sales", headers=H)).json()["items"]]
    assert (await client.delete(f"/api/sales/{sid}", headers=A)).status_code == 200
