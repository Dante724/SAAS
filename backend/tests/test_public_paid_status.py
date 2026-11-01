"""F2 (launch QA 2026-10-19): the track page and portal say what the customer has paid and
still owes, so a settled job reads "Paid" instead of a bare "Total" they may think they owe."""
import pytest


async def _job(client, h):
    loc = (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]
    cid = (await client.post("/api/clients", json={"name": "Ana Lima", "phone": "+15550199"}, headers=h)).json()["id"]
    o = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "cracked"},
                           headers=h)).json()
    await client.post(f"/api/orders/{o['id']}/lines",
                      json={"line_type": "labour", "description": "Screen", "qty": 1, "unit_price": 80}, headers=h)
    ptoken = (await client.post(f"/api/clients/{cid}/portal-link", headers=h)).json()["portal_token"]
    track = (await client.get(f"/api/orders/{o['id']}", headers=h)).json()["public_token"]
    await client.post("/api/cash/accounts", json={"name": "Till", "payment_option": "cash"}, headers=h)
    acc = [a for a in (await client.get("/api/cash/accounts", headers=h)).json()["items"] if a["name"] == "Till"][0]["id"]
    return o["id"], ptoken, track, acc


@pytest.mark.asyncio
async def test_unpaid_then_part_paid_then_paid(client, tenant):
    oid, ptoken, track, acc = await _job(client, tenant)
    page = (await client.get(f"/api/public/track/{track}")).json()
    assert page["amount_paid"] == 0 and page["balance_due"] == 80 and page["paid_in_full"] is False

    r = await client.post(f"/api/cash/orders/{oid}/pay", json={"method": "cash", "account_id": acc, "amount": 30},
                          headers=tenant)
    assert r.status_code == 200, r.text
    page = (await client.get(f"/api/public/track/{track}")).json()
    assert page["amount_paid"] == 30 and page["balance_due"] == 50 and page["paid_in_full"] is False

    r = await client.post(f"/api/cash/orders/{oid}/pay", json={"method": "cash", "account_id": acc}, headers=tenant)
    assert r.status_code == 200, r.text
    page = (await client.get(f"/api/public/track/{track}")).json()
    assert page["amount_paid"] == 80 and page["balance_due"] == 0 and page["paid_in_full"] is True

    # the portal shows the same, on the job page and on the job list
    job = (await client.get(f"/api/public/portal/{ptoken}/order/{oid}")).json()
    assert job["paid_in_full"] is True and job["balance_due"] == 0
    card = next(c for c in (await client.get(f"/api/public/portal/{ptoken}")).json()["work_orders"] if c["id"] == oid)
    assert card["paid_in_full"] is True and card["amount_paid"] == 80 and card["balance_due"] == 0


@pytest.mark.asyncio
async def test_empty_job_is_not_paid_in_full(client, tenant):
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    cid = (await client.post("/api/clients", json={"name": "Bo", "phone": "+15550100"}, headers=tenant)).json()["id"]
    o = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc}, headers=tenant)).json()
    track = (await client.get(f"/api/orders/{o['id']}", headers=tenant)).json()["public_token"]
    page = (await client.get(f"/api/public/track/{track}")).json()
    assert page["paid_in_full"] is False and page["balance_due"] == 0
