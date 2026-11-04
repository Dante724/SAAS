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


async def _legacy_row(db, mapped, /, **values) -> str:
    """Insert a row the way pre-backfill data looks (org_id NULL), bypassing the ORM flush, which
    no longer writes org-less devices or POs. Returns its id."""
    import uuid as _uuid

    from sqlalchemy import insert
    rid = str(_uuid.uuid4())
    await db.execute(insert(mapped.__table__).values(id=rid, org_id=None, **values))
    await db.commit()
    return rid


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


async def test_nullable_org_tables_are_scoped(db, two_shops):
    """Devices and POs carry a nullable org_id (old backfill). Another shop's row is hidden, and so
    is a row with no shop at all: it belongs to nobody, so no shop sees it through the guard."""
    A, B = two_shops
    dev = Device(org_id=B["org"], client_id=B["client"], brand="B brand", model="B model")
    db.add(dev)
    await db.commit()
    orphan = await _legacy_row(db, Device, brand="nobody's", model="x")
    db.expunge_all()
    _as_shop(db, A["org"])
    assert await db.get(Device, dev.id) is None
    assert await db.get(Device, orphan) is None
    assert (await db.execute(select(Device.id).where(Device.org_id.is_(None)))).all() == []


async def test_legacy_asset_reads_only_reach_own_clients_devices(client, db, two_shops):
    """The two legacy reads that still match an org-less device through its owning client
    (scan-resolve, conversions list) opt out narrowly: shop A never gets shop B's org-less device."""
    from backend.routers.industry import _match_existing
    A, B = two_shops
    mine = await _legacy_row(db, Device, client_id=A["client"], brand="A", model="legacy", serial="SN-LEG-1")
    await _legacy_row(db, Device, client_id=B["client"], brand="B", model="legacy", serial="SN-LEG-2")
    db.expunge_all()
    _as_shop(db, A["org"])
    assert (await _match_existing(db, A["org"], "serial", "SN-LEG-1")).id == mine
    assert await _match_existing(db, A["org"], "serial", "SN-LEG-2") is None


async def test_bulk_update_and_delete_are_scoped(client, db, two_shops):
    """ORM bulk UPDATE / DELETE by id never reach another shop's row, and still reach our own."""
    from sqlalchemy import delete, update
    A, B = two_shops
    spare = {}
    for s in (A, B):
        spare[s["org"]] = (await client.post("/api/clients", json={"name": "spare"}, headers=s["h"])).json()["id"]
    _as_shop(db, A["org"])
    r = await db.execute(update(Client).where(Client.id == B["client"]).values(name="pwned"))
    assert r.rowcount == 0
    r = await db.execute(update(Client).where(Client.id == A["client"]).values(name="renamed"))
    assert r.rowcount == 1
    r = await db.execute(delete(Client).where(Client.id == spare[B["org"]]))
    assert r.rowcount == 0
    r = await db.execute(delete(Client).where(Client.id == spare[A["org"]]))
    assert r.rowcount == 1
    await db.commit()
    db.sync_session.info.pop("tenant_org")
    names = dict((await db.execute(select(Client.id, Client.name).where(
        Client.id.in_([A["client"], B["client"], spare[B["org"]]])))).all())
    assert names[A["client"]] == "renamed" and names[B["client"]] != "pwned" and spare[B["org"]] in names


async def test_write_guard_refuses_a_foreign_reference_on_flush(db, two_shops):
    """The flush-time guard on its own (no read filter involved): shop A's order re-pointed at
    shop B's client is refused when flushed."""
    from backend.db.tenancy import CrossTenantReference
    A, B = two_shops
    order = await db.get(Order, A["order"])          # loaded with no tenant: no read filter at all
    _as_shop(db, A["org"])
    order.client_id = B["client"]
    try:
        with pytest.raises(CrossTenantReference):
            await db.flush()
    finally:
        await db.rollback()


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
    from backend.db import tenancy
    from backend.db.database import Base
    from backend.db.tenancy import UNSCOPED_ORG_TABLES, OrgScoped
    missing = []
    for m in Base.registry.mappers:
        col = m.local_table.c.get("org_id")
        if col is None:
            continue
        scoped = issubclass(m.class_, OrgScoped)
        if scoped == (m.local_table.name in UNSCOPED_ORG_TABLES):
            missing.append(m.local_table.name)
    # NULL-org rows are visible to no shop: there is no "shared legacy" variant any more.
    assert not hasattr(tenancy, "OrgScopedLegacyNull")
    from backend.models import PurchaseOrder
    assert issubclass(Device, OrgScoped) and issubclass(PurchaseOrder, OrgScoped)
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


async def test_appointment_ignores_another_shops_service(client, db, two_shops):
    """appointments: the service's duration came from an unfiltered by-id read."""
    A, B = two_shops
    r = await client.post("/api/appointments", json={"scheduled_for": "2031-03-03T10:00:00",
                                                     "service_id": B["service"]}, headers=A["h"])
    # The service lookup is filtered (no 200-minute duration leaks) and the write guard then refuses
    # the foreign service_id: an opaque 404, and nothing is stored.
    assert r.status_code == 404, r.text
    from backend.models import Appointment
    assert (await db.execute(select(Appointment.id).where(Appointment.service_id == B["service"]))).all() == []


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


async def test_resubscribing_a_browser_moves_it_to_the_new_shop(client, db, two_shops):
    """One browser endpoint, signed in at shop B, then at shop A: the subscription moves to A, so
    B's notifications stop going to that browser (it used to stay B's, plus a duplicate for A)."""
    from backend.models import PushSubscription
    A, B = two_shops
    body = {"endpoint": "https://push.example/ep-1", "keys": {"p256dh": "k", "auth": "a"}}
    assert (await client.post("/api/push/subscribe", json=body, headers=B["h"])).status_code == 201
    r = await client.post("/api/push/subscribe", json=body, headers=A["h"])
    assert r.status_code == 201, r.text
    rows = (await db.execute(select(PushSubscription.org_id, PushSubscription.employee_id).where(
        PushSubscription.endpoint == body["endpoint"]))).all()
    assert rows == [(A["org"], A["emp"])]
    assert (await db.execute(select(PushSubscription.id).where(
        PushSubscription.employee_id == B["emp"]))).all() == []


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


# ── migration trg01: give org-less devices and POs their shop ────────────────
async def test_backfill_gives_orgless_devices_and_pos_their_shop(client, db, two_shops):
    import importlib

    from backend.models import PurchaseOrder, Supplier
    mod = importlib.import_module("backend.migrations.versions.trg01_backfill_device_po_org")
    A, B = two_shops
    loc_b = await _loc(client, B["h"])
    via_client = await _legacy_row(db, Device, client_id=A["client"], brand="x", model="y")
    via_location = await _legacy_row(db, Device, location_id=loc_b, brand="x", model="y")
    unknown = await _legacy_row(db, Device, brand="x", model="y")
    sup = Supplier(org_id=A["org"], name="Sup A")
    db.add(sup)
    await db.commit()
    po = await _legacy_row(db, PurchaseOrder, supplier_id=sup.id, location_id=loc_b, po_number="PO-LEG-1",
                           status="ordered")
    await db.run_sync(lambda s: (mod._fill(s.connection(), "devices", mod._DEVICE_SOURCES),
                                 mod._fill(s.connection(), "purchase_orders", mod._PO_SOURCES)))
    await db.commit()
    db.expunge_all()
    got = dict((await db.execute(select(Device.id, Device.org_id).where(
        Device.id.in_([via_client, via_location, unknown])))).all())
    assert got == {via_client: A["org"], via_location: B["org"], unknown: None}   # nothing deleted
    # The location is the reliable source for a PO (the supplier here is A's, the location B's).
    assert (await db.execute(select(PurchaseOrder.org_id).where(
        PurchaseOrder.id == po))).scalar_one() == B["org"]


async def test_new_devices_and_pos_never_written_without_a_shop(client, db, two_shops):
    """A device or PO saved without org_id takes its shop from its links (client / location /
    supplier), the same rules as trg01, so no new row is invisible to every shop."""
    from backend.models import PurchaseOrder, Supplier
    A, B = two_shops
    dev = Device(client_id=B["client"], brand="x", model="y")
    sup = Supplier(org_id=A["org"], name="Sup A")
    db.add_all([dev, sup])
    await db.flush()
    po = PurchaseOrder(supplier_id=sup.id, location_id=await _loc(client, A["h"]), po_number="PO-NEW-1",
                       status="ordered")
    db.add(po)
    await db.commit()
    assert (dev.org_id, po.org_id) == (B["org"], A["org"])
