"""Wave 4, group F2 (infra/perf): the smaller follow-ups.

- PERF-17: shops the integrity sweep couldn't check (`reconcile.LAST_FAILED`) are a /metrics gauge,
  so an alert can fire on a sweep that silently stopped checking.
- Flush FK guard (db/tenancy.py): it walked the whole identity map on every flush. A bulk import
  autoflushes per row in a session holding thousands of rows, so that was O(rows^2). It now looks up
  only the referenced ids (identity-key lookups) and the new/modified rows of the referenced
  tables, with the same protection.
- Parts list: a photo PIL can't read showed no picture at all; the list falls back to the photo.
- corg01's SQLite rebuild of stock_levels: the partial unique index is present at head.
"""
import base64
import sqlite3
import uuid

import pytest
from sqlalchemy import event, select

pytestmark = pytest.mark.asyncio


# ── PERF-17 ─────────────────────────────────────────────────────────────────────────────────
async def test_metrics_exposes_shops_the_sweep_could_not_check(monkeypatch):
    from backend.core.observability import render_metrics
    from backend.services import reconcile
    monkeypatch.setattr(reconcile, "LAST_COUNTS", {"client": 0})
    monkeypatch.setattr(reconcile, "LAST_FAILED", 3)
    out = render_metrics()
    assert "# TYPE repairos_integrity_failed_shops gauge" in out
    assert "repairos_integrity_failed_shops 3" in out
    monkeypatch.setattr(reconcile, "LAST_FAILED", 0)
    assert "repairos_integrity_failed_shops 0" in render_metrics()


async def test_metrics_failed_gauge_follows_a_real_sweep(db, monkeypatch, client, tenant):
    from backend.core.observability import render_metrics
    from backend.services import reconcile

    async def boom(*a, **kw):
        raise RuntimeError("statement timeout")
    monkeypatch.setattr(reconcile, "find_mismatches", boom)
    await reconcile.sweep(db)
    assert reconcile.LAST_FAILED >= 1
    assert f"repairos_integrity_failed_shops {reconcile.LAST_FAILED}" in render_metrics()
    monkeypatch.setattr(reconcile, "LAST_FAILED", 0)


# ── flush FK guard ──────────────────────────────────────────────────────────────────────────
async def _register(client, name):
    r = await client.post("/api/auth/register", json={
        "org_name": name, "name": "Owner", "email": f"w4g-{uuid.uuid4().hex[:8]}@test.dev",
        "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    me = (await client.get("/api/auth/me", headers=h)).json()
    return {"h": h, "org": me["org_id"]}


@pytest.fixture
async def two_shops(client):
    out = []
    for name in ("Shop A", "Shop B"):
        s = await _register(client, name)
        h = s["h"]
        loc = (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]
        s["loc"] = loc
        s["client"] = (await client.post("/api/clients", json={"name": f"{name} c"}, headers=h)).json()["id"]
        s["order"] = (await client.post("/api/orders", json={"client_id": s["client"], "location_id": loc,
                                                              "complaint": "x"}, headers=h)).json()["id"]
        out.append(s)
    return out


class _MapWalks:
    """Counts full walks of a session's identity map (values/items/keys/iteration)."""

    def __init__(self, monkeypatch, session):
        # the identity map's class moved between SQLAlchemy releases (2.0: WeakInstanceDict);
        # spy on whatever class this session actually uses
        WeakInstanceDict = type(session.identity_map)
        self.n = 0
        for name in ("values", "items", "keys", "__iter__", "all_states"):
            real = getattr(WeakInstanceDict, name, None)
            if real is None:
                continue

            def spy(self_, *a, _real=real, **kw):
                self.n += 1
                return _real(self_, *a, **kw)
            monkeypatch.setattr(WeakInstanceDict, name, spy)


async def test_flush_guard_does_not_walk_the_identity_map(db, two_shops, monkeypatch):
    from backend.models import Client, Order
    A, B = two_shops
    # a session holding many loaded rows, as during a bulk import
    bulk = [Client(org_id=A["org"], name="bulk") for _ in range(40)]
    db.add_all(bulk)
    await db.flush()
    (await db.execute(select(Client))).scalars().all()
    order = await db.get(Order, A["order"])
    db.sync_session.info["tenant_org"] = A["org"]
    db.sync_session.info.pop("tenant_known", None)
    order.client_id = bulk[0].id                        # a real FK change: the guard has work to do
    walks = _MapWalks(monkeypatch, db.sync_session)
    try:
        await db.flush()
        assert walks.n == 0, f"identity map walked {walks.n} time(s) on one flush"
    finally:
        await db.rollback()


async def test_flush_guard_still_refuses_a_loaded_foreign_parent_without_sql(db, two_shops):
    """The owner of a referenced row the session already holds is read from the session."""
    from backend.db.tenancy import CrossTenantReference
    from backend.models import Client, Order
    A, B = two_shops
    order = await db.get(Order, A["order"])
    held = await db.get(Client, B["client"])            # loaded (and held), so no SELECT is needed
    assert held is not None
    db.sync_session.info["tenant_org"] = A["org"]
    db.sync_session.info.pop("tenant_known", None)
    order.client_id = B["client"]
    seen = []

    def hit(conn, cur, stmt, *a):
        seen.append(stmt)
    event.listen(db.bind.sync_engine, "before_cursor_execute", hit)
    try:
        with pytest.raises(CrossTenantReference):
            await db.flush()
        assert not [s for s in seen if "FROM clients" in s], seen
    finally:
        event.remove(db.bind.sync_engine, "before_cursor_execute", hit)
        await db.rollback()


async def test_flush_guard_refuses_a_foreign_parent_not_yet_loaded(db, two_shops):
    from backend.db.tenancy import CrossTenantReference
    from backend.models import Order
    A, B = two_shops
    order = await db.get(Order, A["order"])
    db.sync_session.info["tenant_org"] = A["org"]
    db.sync_session.info.pop("tenant_known", None)
    order.client_id = B["client"]
    try:
        with pytest.raises(CrossTenantReference):
            await db.flush()
    finally:
        await db.rollback()


async def test_flush_guard_refuses_a_pending_foreign_parent_in_the_same_flush(db, two_shops):
    """No tenant (a script): shop A's job pointed at a client of shop B created in the same flush.
    The pending client isn't in the identity map or the database yet; it is in session.new."""
    from backend.db.tenancy import CrossTenantReference
    from backend.models import Client, Order
    A, B = two_shops
    order = await db.get(Order, A["order"])
    newc = Client(id=str(uuid.uuid4()), org_id=B["org"], name="pending")
    db.add(newc)
    order.client_id = newc.id
    try:
        with pytest.raises(CrossTenantReference):
            await db.flush()
    finally:
        await db.rollback()


async def test_flush_guard_lets_own_references_through(db, two_shops):
    from backend.models import Client, Order
    A, B = two_shops
    order = await db.get(Order, A["order"])
    db.sync_session.info["tenant_org"] = A["org"]
    c = Client(org_id=A["org"], name="mine")
    db.add(c)
    await db.flush()
    order.client_id = c.id
    try:
        await db.flush()
    finally:
        await db.rollback()


# ── parts list thumbnail fallback ──────────────────────────────────────────────────────────
async def test_parts_list_shows_the_photo_when_pil_cannot_read_it(client, tenant, db):
    from backend.models import Part
    from backend.routers import catalog_products
    # a well-formed data URL whose bytes no image library can decode
    unreadable = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\nnot really").decode()
    pid = (await client.post("/api/inventory/parts", headers=tenant,
                             json={"name": "Odd photo", "sku": "ODD-1"})).json()["id"]
    (await db.execute(select(Part).where(Part.id == pid))).scalar_one().image_data = unreadable
    await db.commit()
    catalog_products._THUMBS.clear()
    for _ in range(2):                                  # cold, then warm cache
        items = (await client.get("/api/inventory/parts", headers=tenant)).json()["items"]
        row = next(i for i in items if i["id"] == pid)
        assert row["image_data"] == unreadable


async def test_unreadable_photo_too_big_for_a_list_gets_a_placeholder(client, tenant, db, monkeypatch):
    from backend.models import Part
    from backend.routers import catalog_products
    monkeypatch.setattr(catalog_products, "THUMB_FALLBACK_MAX", 10)
    unreadable = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\nnot really").decode()
    pid = (await client.post("/api/inventory/parts", headers=tenant,
                             json={"name": "Odd photo", "sku": "ODD-2"})).json()["id"]
    (await db.execute(select(Part).where(Part.id == pid))).scalar_one().image_data = unreadable
    await db.commit()
    catalog_products._THUMBS.clear()
    items = (await client.get("/api/inventory/parts", headers=tenant)).json()["items"]
    row = next(i for i in items if i["id"] == pid)
    assert row["image_data"] == catalog_products.THUMB_PLACEHOLDER
    assert row["image_data"].startswith("data:image/svg+xml") is False   # never an SVG (script-free types only)


# ── corg01 / w2b01: SQLite keeps the stock_levels partial unique index ──────────────────────
def test_stock_levels_unbinned_unique_index_exists_at_head_on_sqlite(tmp_path):
    from backend.tests.test_tk01_migration import _alembic
    path = tmp_path / "head.db"
    _alembic(f"sqlite+aiosqlite:///{path}", "upgrade", "head")
    con = sqlite3.connect(path)
    row = con.execute("SELECT sql FROM sqlite_master WHERE type='index' AND name='uq_stock_levels_part_loc_unbinned'"
                      ).fetchone()
    con.close()
    assert row is not None, "uq_stock_levels_part_loc_unbinned missing at head"
    sql = " ".join(row[0].split()).lower()
    assert "unique" in sql and "where" in sql and "bin_id is null" in sql
