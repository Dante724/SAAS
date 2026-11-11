"""Launch recheck 2 (B10 / B7): doors still open after the first fixes.

* PATCH on an order line changed qty with no permission check: a Viewer or an invitee with no
  role cut a 4,500 line to 45.
* An invitee with no role could bulk-restore archived jobs (`if current.role_id:` read "no role"
  as "no limits").
* Settings showed Stripe/Square/SumUp as set up ("Shared default") from the operator's env keys,
  which card payments never use.
* A shop's own webhook URL accepted an event signed with the operator-wide secret."""
import hashlib
import hmac
import json
import time

import pytest
from sqlalchemy import select

from backend.config import settings
from backend.models import Employee, Invite, PaymentIntent, Role
from backend.services import org_creds
from backend.services.auth import hash_password


async def _me(client, h):
    return (await client.get("/api/auth/me", headers=h)).json()


async def _login(client, email, pw="pw-staff-12345"):
    r = await client.post("/api/auth/token", data={"username": email, "password": pw})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _staff(client, tenant, db, name, perms):
    me = await _me(client, tenant)
    role = Role(org_id=me["org_id"], name=name, permissions=perms, perms_explicit=True)
    db.add(role)
    await db.flush()
    email = f"{name.lower()}@r2.dev"
    db.add(Employee(org_id=me["org_id"], location_id=me["location_id"], role_id=role.id, name=name,
                    email=email, password_hash=hash_password("pw-staff-12345"), active=True))
    await db.commit()
    return await _login(client, email)


async def _roleless(client, tenant, db):
    r = await client.post("/api/staff/invites", json={"email": "norole@r2.dev"}, headers=tenant)
    assert r.status_code == 201, r.text
    inv = (await db.execute(select(Invite).where(Invite.email == "norole@r2.dev"))).scalar_one()
    inv.token_hash = hashlib.sha256(b"r2-token").hexdigest()
    await db.commit()
    r = await client.post("/api/auth/invite/accept", json={"token": "r2-token", "name": "No Role",
                                                           "password": "pw-norole-12345"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _order_with_line(client, tenant, **line):
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    oid = (await client.post("/api/orders", json={"location_id": loc}, headers=tenant)).json()["id"]
    body = {"kind": "service", "description": "Board repair", "qty": 1, "unit_price": 4500, **line}
    r = await client.post(f"/api/orders/{oid}/lines", json=body, headers=tenant)
    assert r.status_code in (200, 201), r.text
    lid = (await client.get(f"/api/orders/{oid}", headers=tenant)).json()["lines"][0]["id"]
    return oid, lid


@pytest.mark.asyncio
async def test_line_qty_change_needs_the_add_right(client, tenant, db):
    viewer = await _staff(client, tenant, db, "Viewer", {})
    nobody = await _roleless(client, tenant, db)
    oid, lid = await _order_with_line(client, tenant)
    for h in (viewer, nobody):
        r = await client.patch(f"/api/orders/{oid}/lines/{lid}", json={"qty": 0.01}, headers=h)
        assert r.status_code == 403, r.text
    line = (await client.get(f"/api/orders/{oid}", headers=tenant)).json()["lines"][0]
    assert float(line["qty"]) == 1.0
    # resending the same qty isn't a change; the owner can change it
    assert (await client.patch(f"/api/orders/{oid}/lines/{lid}", json={"qty": 1}, headers=viewer)).status_code == 200
    assert (await client.patch(f"/api/orders/{oid}/lines/{lid}", json={"qty": 2}, headers=tenant)).status_code == 200


@pytest.mark.asyncio
async def test_line_qty_right_follows_the_line_kind(client, tenant, db):
    tech = await _staff(client, tenant, db, "Tech", {"can_add_services": True})
    oid, svc = await _order_with_line(client, tenant)
    r = await client.post(f"/api/orders/{oid}/lines", json={"kind": "product", "description": "Screen",
                                                            "qty": 1, "unit_price": 300}, headers=tenant)
    assert r.status_code in (200, 201), r.text
    prod = next(x["id"] for x in (await client.get(f"/api/orders/{oid}", headers=tenant)).json()["lines"]
                if x["kind"] == "product")
    assert (await client.patch(f"/api/orders/{oid}/lines/{prod}", json={"qty": 0.5}, headers=tech)).status_code == 403
    assert (await client.patch(f"/api/orders/{oid}/lines/{svc}", json={"qty": 2}, headers=tech)).status_code == 200


@pytest.mark.asyncio
async def test_roleless_invitee_cannot_bulk_restore(client, tenant, db):
    nobody = await _roleless(client, tenant, db)
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    oid = (await client.post("/api/orders", json={"location_id": loc}, headers=tenant)).json()["id"]
    r = await client.post(f"/api/orders/{oid}/archive", json={"reason": "dupe", "refund": False}, headers=tenant)
    assert r.status_code == 200, r.text

    r = await client.post("/api/orders/bulk", json={"order_ids": [oid], "action": "restore"}, headers=nobody)
    assert r.status_code == 403, r.text
    arch = (await client.get("/api/orders/archived", headers=tenant)).json()
    assert any(x["id"] == oid for x in arch["items"])
    r = await client.post("/api/orders/bulk", json={"order_ids": [oid], "action": "restore"}, headers=tenant)
    assert r.status_code == 200 and r.json()["updated"] == 1


@pytest.mark.asyncio
async def test_operator_card_keys_do_not_show_as_set_up(monkeypatch, client, tenant, db):
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_operator", raising=False)
    monkeypatch.setattr(settings, "SQUARE_ACCESS_TOKEN", "sq-operator", raising=False)
    monkeypatch.setattr(settings, "SUMUP_API_KEY", "sup_operator", raising=False)
    got = (await client.get("/api/integrations/credentials", headers=tenant)).json()
    for p in ("stripe", "square", "sumup"):
        assert got[p]["source"] == "none" and got[p]["configured"] is False, (p, got[p])

    me = await _me(client, tenant)
    await org_creds.set_creds(db, me["org_id"], "stripe", {"secret_key": "sk_test_shop"})
    got = (await client.get("/api/integrations/credentials", headers=tenant)).json()
    assert got["stripe"]["source"] == "org" and got["stripe"]["configured"] is True


def _stripe_sig(secret, raw):
    ts = str(int(time.time()))
    v1 = hmac.new(secret.encode(), f"{ts}.".encode() + raw, hashlib.sha256).hexdigest()
    return f"t={ts},v1={v1}"


@pytest.mark.asyncio
async def test_shop_webhook_url_takes_only_the_shops_own_secret(monkeypatch, client, tenant, db):
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", "whsec_operator", raising=False)
    me = await _me(client, tenant)
    i = PaymentIntent(org_id=me["org_id"], amount=15.0, currency="usd", status="pending",
                      provider="stripe", provider_ref="cs_r2")
    db.add(i)
    await db.commit()
    await db.refresh(i)
    await org_creds.set_creds(db, i.org_id, "stripe", {"secret_key": "sk_test_x", "webhook_secret": "whsec_shop"})
    raw = json.dumps({"type": "checkout.session.completed", "data": {"object": {
        "metadata": {"intent_id": i.id, "org_id": i.org_id}, "client_reference_id": i.id,
        "amount_total": 1500, "currency": "usd", "payment_status": "paid"}}}).encode()
    url = f"/api/payments/stripe/webhook/{i.org_id}"

    async def post(secret):
        return await client.post(url, content=raw, headers={"content-type": "application/json",
                                                            "stripe-signature": _stripe_sig(secret, raw)})

    assert (await post("whsec_operator")).status_code == 400
    assert (await client.get(f"/api/public/pay/{i.id}")).json()["status"] != "succeeded"
    assert (await post("whsec_shop")).status_code == 200
    assert (await client.get(f"/api/public/pay/{i.id}")).json()["status"] == "succeeded"
