"""Read-side tenant isolation (architecture hardening: tenant read guard).

Before this, every read relied on a hand-written ``Model.org_id ==`` filter; a query that forgot it
returned another shop's rows. Now, once a request knows its shop (``session.info["tenant_org"]``),
every SELECT of an ``OrgScoped`` model only sees that shop's rows (backend/db/tenancy.py).

Legit cross-shop reads opt out with ``execution_options(skip_tenant_filter=True)``; sessions with
no shop (platform console, schedulers, migrations) are not filtered at all.
"""
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import aliased

from backend.models import Client, Device, Employee, Order, Part, Service

pytestmark = pytest.mark.asyncio


async def _register(client, name):
    r = await client.post("/api/auth/register", json={
        "org_name": name, "name": "Owner", "email": f"rg-{uuid.uuid4().hex[:8]}@test.dev",
        "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    me = (await client.get("/api/auth/me", headers=h)).json()
    return {"h": h, "org": me["org_id"], "emp": me["id"]}


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


@pytest.fixture
async def two_shops(client):
    """Shop A and shop B, each with a client, a product, a service and a work order."""
    out = []
    for name in ("Shop A", "Shop B"):
        s = await _register(client, name)
        h = s["h"]
        s["client"] = (await client.post("/api/clients", json={"name": f"{name} client"}, headers=h)).json()["id"]
        r = await client.post("/api/catalog/products", json={"name": f"{name} part", "sell_price": 10,
                                                             "cost_price": 4}, headers=h)
        assert r.status_code == 201, r.text
        s["part"] = r.json()["id"]
        r = await client.post("/api/catalog/services", json={"name": f"{name} svc", "price": 10,
                                                             "duration_min": 200}, headers=h)
        assert r.status_code in (200, 201), r.text
        s["service"] = r.json()["id"]
        r = await client.post("/api/orders", json={"client_id": s["client"], "location_id": await _loc(client, h),
                                                   "complaint": "x"}, headers=h)
        assert r.status_code == 201, r.text
        s["order"] = r.json()["id"]
        out.append(s)
    return out


def _as_shop(db, org_id):
    db.sync_session.info["tenant_org"] = org_id


# ── the mechanism ────────────────────────────────────────────────────────────
async def test_get_by_id_of_another_shops_row_returns_none(db, two_shops):
    A, B = two_shops
    _as_shop(db, A["org"])
    for model, key in ((Client, "client"), (Part, "part"), (Service, "service"), (Order, "order"),
                       (Employee, "emp")):
        assert await db.get(model, B[key]) is None, model.__name__
        assert await db.get(model, A[key]) is not None, model.__name__
    # The shop id is a bound parameter, not baked into a cached statement: switch shops.
    db.expunge_all()
    _as_shop(db, B["org"])
    assert await db.get(Client, A["client"]) is None
    assert await db.get(Client, B["client"]) is not None


async def test_select_of_another_shops_rows_is_empty(db, two_shops):
    A, B = two_shops
    _as_shop(db, A["org"])
    # No hand-written org filter anywhere here: the guard adds it.
    assert (await db.execute(select(Client).where(Client.id == B["client"]))).scalar_one_or_none() is None
    assert (await db.execute(select(Part.name).where(Part.id == B["part"]))).first() is None
    ids = set((await db.execute(select(Order.id))).scalars())
    assert A["order"] in ids and B["order"] not in ids
    # Joins and aliases are scoped too.
    o2 = aliased(Order)
    joined = (await db.execute(select(Client.id).join(o2, o2.client_id == Client.id))).scalars().all()
    assert joined == [A["client"]]


async def test_legacy_nullable_org_tables_are_scoped(db, two_shops):
    """Devices carry a nullable org_id (backfill); another shop's device is still hidden."""
    A, B = two_shops
    dev = Device(org_id=B["org"], client_id=B["client"], brand="B brand", model="B model")
    db.add(dev)
    await db.commit()
    db.expunge_all()
    _as_shop(db, A["org"])
    assert await db.get(Device, dev.id) is None


async def test_explicit_opt_out_reads_across_shops(db, two_shops):
    A, B = two_shops
    _as_shop(db, A["org"])
    row = (await db.execute(select(Client).where(Client.id == B["client"])
                            .execution_options(skip_tenant_filter=True))).scalar_one_or_none()
    assert row is not None and row.org_id == B["org"]
    db.expunge_all()
    assert await db.get(Part, B["part"], execution_options={"skip_tenant_filter": True}) is not None


async def test_no_tenant_means_no_filter(db, two_shops):
    """Schedulers, migrations and the platform console have no shop: they see every shop."""
    A, B = two_shops
    assert "tenant_org" not in db.sync_session.info
    orgs = set((await db.execute(select(Client.org_id))).scalars())
    assert {A["org"], B["org"]} <= orgs


async def test_every_org_table_is_classified():
    """A new model with an org_id must be OrgScoped, or listed as an exception with a reason."""
    from backend.db.database import Base
    from backend.db.tenancy import UNSCOPED_ORG_TABLES, OrgScoped, OrgScopedLegacyNull
    missing = []
    for m in Base.registry.mappers:
        col = m.local_table.c.get("org_id")
        if col is None:
            continue
        scoped = issubclass(m.class_, (OrgScoped, OrgScopedLegacyNull))
        if scoped == (m.local_table.name in UNSCOPED_ORG_TABLES):
            missing.append(m.local_table.name)
        if issubclass(m.class_, OrgScoped):
            assert not col.nullable, f"{m.local_table.name}: OrgScoped needs a non-null org_id"
    assert missing == []


# ── through the app ──────────────────────────────────────────────────────────
async def test_platform_admin_reads_every_shop(client, two_shops):
    from backend.tests.admin_helpers import login, seed_admin
    A, B = two_shops
    H = await login(client, await seed_admin())
    for s in (A, B):
        r = await client.get(f"/api/admin/tenants/{s['org']}", headers=H)
        assert r.status_code == 200, r.text
        assert [e["id"] for e in r.json()["employees_detail"]] == [s["emp"]]


async def test_staff_email_stays_unique_across_shops(client, two_shops):
    """A legit cross-shop read: one email is one Tekly login, so adding shop B's owner's email to
    shop A must still be refused (it would otherwise 500 on the unique index)."""
    A, B = two_shops
    b_email = (await client.get("/api/auth/me", headers=B["h"])).json()["email"]
    r = await client.post("/api/staff/employees", json={"name": "Dup", "email": b_email,
                                                        "password": "pw-test-12345"}, headers=A["h"])
    assert r.status_code == 400, r.text


async def test_appointment_ignores_another_shops_service(client, two_shops):
    """appointments: the service's duration came from an unfiltered by-id read."""
    A, B = two_shops
    r = await client.post("/api/appointments", json={"scheduled_for": "2031-03-03T10:00:00",
                                                     "service_id": B["service"]}, headers=A["h"])
    if r.status_code in (201, 200):
        assert r.json().get("duration_min") != 200, r.text
    else:
        assert r.status_code in (404, 422), r.text


async def test_public_checkin_for_shop_a_cannot_read_shop_b(client, db, two_shops, monkeypatch):
    """Once a public check-in has resolved shop A's QR code, the request is scoped to shop A."""
    from backend.models import CheckinPoint
    from backend.services import checkin as svc
    A, B = two_shops
    await svc.ensure_points(db, A["org"])
    await db.commit()
    point = (await db.execute(select(CheckinPoint).where(CheckinPoint.org_id == A["org"]))).scalars().first()
    assert point is not None
    seen = {}
    real = svc.effective_mode

    async def spy(sdb, p):
        seen["tenant"] = sdb.sync_session.info.get("tenant_org")
        seen["b_client"] = await sdb.get(Client, B["client"])
        seen["b_parts"] = (await sdb.execute(select(Part.id).where(Part.id == B["part"]))).all()
        return await real(sdb, p)

    monkeypatch.setattr(svc, "effective_mode", spy)
    await client.post(f"/api/public/checkin/{point.token}", json={"name": "Walk In", "phone": "+15550101",
                                                           "issue": "broken"})
    assert seen.get("tenant") == A["org"]
    assert seen["b_client"] is None and seen["b_parts"] == []


async def test_public_track_page_is_scoped_to_its_shop(client, db, two_shops):
    from backend.routers import public
    A, B = two_shops
    tok = (await db.execute(select(Order.public_token).where(Order.id == A["order"]))).scalar_one()
    order = await public._load(db, tok)
    assert order.id == A["order"]
    assert db.sync_session.info.get("tenant_org") == A["org"]
    assert await db.get(Client, B["client"]) is None


# ── report-only mode ─────────────────────────────────────────────────────────
async def test_report_mode_counts_but_does_not_filter(db, two_shops, monkeypatch):
    from backend.db import tenancy
    A, B = two_shops
    monkeypatch.setattr(tenancy, "MODE", "report")
    tenancy.cross_org_reads.clear()
    _as_shop(db, A["org"])
    row = (await db.execute(select(Client).where(Client.id == B["client"]))).scalar_one_or_none()
    assert row is not None                      # report-only: still returned …
    assert ("clients", B["org"], A["org"]) in tenancy.cross_org_reads   # … but recorded


async def test_enforce_mode_records_nothing_for_own_rows(db, two_shops):
    from backend.db import tenancy
    A, _ = two_shops
    tenancy.cross_org_reads.clear()
    _as_shop(db, A["org"])
    assert await db.get(Client, A["client"]) is not None
    assert tenancy.cross_org_reads == []
