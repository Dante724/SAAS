"""UI audit (Part A) fixes that live on the server: Home money totals stay inside the shop,
the tracking page uses the shop's status names and stops the progress bar for a dropped job,
and the invoice list names the customer."""
import uuid
from datetime import datetime

import pytest

from backend.models import Account, Payment
from backend.services.dates import now_utc


async def _shop(client):
    r = await client.post("/api/auth/register", json={"org_name": "Shop", "name": "Owner",
                                                      "email": f"o-{uuid.uuid4().hex[:8]}@test.dev", "password": "pw-test-12345"})
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    org = (await client.get("/api/auth/me", headers=h)).json()["org_id"]
    return h, org


async def _job(client, h, name="Sam Rivera"):
    loc = (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]
    cid = (await client.post("/api/clients", json={"name": name, "phone": "+15550101"}, headers=h)).json()["id"]
    return (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x"},
                              headers=h)).json()["id"], cid


@pytest.mark.asyncio
async def test_home_money_totals_only_count_this_shop(client, db):
    a, _ = await _shop(client)
    b, org_b = await _shop(client)
    acc = Account(org_id=org_b, name="Till", payment_option="cash")
    db.add(acc)
    await db.flush()
    db.add_all([Payment(account_id=acc.id, method="cash", kind="payment", amount=500, paid_at=now_utc()),
                Payment(account_id=acc.id, method="cash", kind="refund", amount=40, paid_at=now_utc())])
    await db.commit()
    da = (await client.get("/api/reports/company-dashboard", headers=a)).json()
    assert da["monthly"]["revenue"] == 0 and da["today"]["refunds"] == 0 and da["today"]["revenue"] == 0
    dbb = (await client.get("/api/reports/company-dashboard", headers=b)).json()
    assert dbb["monthly"]["revenue"] == 500 and dbb["today"]["refunds"] == 40


@pytest.mark.asyncio
async def test_tracking_page_for_a_dropped_job(client):
    h, _ = await _shop(client)
    await client.get("/api/statuses", headers=h)            # the shop's statuses, as the app loads them
    oid, _ = await _job(client, h)
    assert (await client.patch(f"/api/orders/{oid}/status", json={"status": "dropped", "reason": "No show"},
                               headers=h)).status_code == 200
    token = (await client.get(f"/api/orders/{oid}", headers=h)).json()["public_token"]
    t = (await client.get(f"/api/public/track/{token}")).json()
    assert t["stopped"] is True and t["stage_label"] == "Not collected"
    assert "Not collected" in [x["label"] for x in t["timeline"]]
    assert "dropped" not in [x["label"] for x in t["timeline"]]


@pytest.mark.asyncio
async def test_tracking_page_for_an_open_job_is_not_stopped(client):
    h, _ = await _shop(client)
    oid, _ = await _job(client, h)
    token = (await client.get(f"/api/orders/{oid}", headers=h)).json()["public_token"]
    t = (await client.get(f"/api/public/track/{token}")).json()
    assert t["stopped"] is False and t["stage_index"] == 0 and t["stage_label"] == "Received"


@pytest.mark.asyncio
async def test_invoice_list_names_the_customer(client):
    h, _ = await _shop(client)
    other, _ = await _shop(client)
    _, cid = await _job(client, h, "Pat Builder")
    r = await client.post("/api/finance/invoices", json={"client_id": cid, "lines": [
        {"description": "Bench fee", "qty": 1, "unit_price": 20}]}, headers=h)
    assert r.status_code in (200, 201), r.text
    items = (await client.get("/api/finance/invoices", headers=h)).json()["items"]
    assert items[0]["client_name"] == "Pat Builder" and items[0]["created_at"]
    assert (await client.get("/api/finance/invoices", headers=other)).json()["items"] == []
