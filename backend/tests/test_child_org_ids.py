"""Audit 2026-11-12 DB-01, DB-02, DB-10, PERF-09, SEC-01: every tenant table carries its own shop.

Before this, payments and 22 child tables (order/invoice/sale/stock lines, cash and attendance
shifts, stock levels and movements, chat messages, …) had no ``org_id``. Their shop was guessed
from parent links, every reader guessed differently, the database accepted a payment linked to
nothing, and the tenant read guard (backend/db/tenancy.py) could not filter them at all.

Now each one has ``org_id`` NOT NULL, filled centrally from its parent before every flush
(``tenancy.ORG_PARENTS``), and is ``OrgScoped``, so another shop's row is invisible.
"""
import sqlite3
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from backend.db.database import Base
from backend.models import (
    CashShift,
    Location,
    Order,
    OrderLine,
    Part,
    Payment,
    StockLevel,
    StockMovement,
)

# Tables that legitimately have no org_id: the shop table itself, the platform console, and auth
# rows keyed by token/session. Every other table must carry its shop.
ROOT_TABLES = {"organisations", "platform_admins", "platform_coupons", "admin_roles", "admin_sessions",
               "auth_tokens", "revoked_sessions"}

CHILD_TABLES = {
    "payments", "order_lines", "order_comments", "order_status_logs", "invoice_lines", "invoice_orders",
    "credit_note_lines", "sale_lines", "cash_shifts", "shifts", "wage_records", "chat_messages",
    "stock_levels", "stock_movements", "stock_transfer_lines", "stocktake_lines", "stocktake_serials",
    "po_lines", "posting_lines", "purchase_return_lines", "write_off_lines", "client_return_lines",
    "bundle_components",
}


async def _register(client, name):
    r = await client.post("/api/auth/register", json={
        "org_name": name, "name": "Owner", "email": f"co-{uuid.uuid4().hex[:8]}@test.dev",
        "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    me = (await client.get("/api/auth/me", headers=h)).json()
    return {"h": h, "org": me["org_id"], "emp": me["id"]}


@pytest.fixture
async def two_shops(client):
    out = []
    for name in ("Shop A", "Shop B"):
        s = await _register(client, name)
        h = s["h"]
        s["loc"] = (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]
        s["client"] = (await client.post("/api/clients", json={"name": f"{name} client"}, headers=h)).json()["id"]
        r = await client.post("/api/catalog/products", json={"name": f"{name} part", "sell_price": 10,
                                                             "cost_price": 4}, headers=h)
        assert r.status_code == 201, r.text
        s["part"] = r.json()["id"]
        r = await client.post("/api/orders", json={"client_id": s["client"], "location_id": s["loc"],
                                                   "complaint": "x"}, headers=h)
        assert r.status_code == 201, r.text
        s["order"] = r.json()["id"]
        out.append(s)
    return out


def _as_shop(db, org_id):
    db.sync_session.info["tenant_org"] = org_id


# ── schema ──────────────────────────────────────────────────────────────────
async def test_every_tenant_table_has_a_non_null_indexed_org_id():
    from backend.db.tenancy import ORG_PARENTS, OrgScoped
    from backend.models import all_models  # noqa: F401
    missing = sorted(t for t, tab in Base.metadata.tables.items()
                     if "org_id" not in tab.c and t not in ROOT_TABLES)
    assert missing == []
    by_table = {m.local_table.name: m.class_ for m in Base.registry.mappers}
    for t in CHILD_TABLES:
        col = Base.metadata.tables[t].c.org_id
        assert not col.nullable, t
        assert [fk.column.table.name for fk in col.foreign_keys] == ["organisations"], t
        assert any(ix.columns.keys()[0] == "org_id" for ix in col.table.indexes), t
        assert issubclass(by_table[t], OrgScoped), t
        assert t in ORG_PARENTS, t


async def test_hot_payment_indexes_exist():
    """DB-10 / PERF-09: drawer close sums payments by shift; money reports range a shop's paid_at."""
    ix = {tuple(i.columns.keys()) for i in Payment.__table__.indexes}
    assert ("shift_id",) in ix
    assert ("org_id", "paid_at") in ix
    assert ("received_by_id",) in ix


# ── writes: the shop comes from the parent, centrally ───────────────────────
async def test_payment_takes_its_shop_from_its_links(db, two_shops):
    A, B = two_shops
    for links in ({"order_id": A["order"]}, {"client_id": A["client"]}):
        p = Payment(kind="payment", amount=5, method="cash", **links)
        db.add(p)
        await db.commit()
        assert p.org_id == A["org"], links


async def test_payment_linked_to_nothing_is_refused(db):
    db.add(Payment(kind="payment", amount=5, method="cash"))
    with pytest.raises(IntegrityError):
        await db.commit()
    await db.rollback()


async def test_mixed_shop_payment_is_refused_without_a_tenant(db, two_shops):
    """A's job and B's client: the payment is A's (from the job), and B's client is refused."""
    from backend.db.tenancy import CrossTenantReference
    A, B = two_shops
    db.add(Payment(kind="payment", amount=5, method="cash", order_id=A["order"], client_id=B["client"]))
    with pytest.raises(CrossTenantReference):
        await db.flush()
    await db.rollback()


async def test_child_added_through_a_pending_parent_gets_its_shop(db, two_shops):
    A, _ = two_shops
    o = Order(org_id=A["org"], location_id=A["loc"], client_id=A["client"], order_number=f"T-{uuid.uuid4().hex[:6]}")
    line = OrderLine(line_type="part", description="screen", qty=1, unit_price=10)
    o.lines.append(line)
    db.add(o)
    await db.commit()
    assert line.org_id == A["org"]


async def test_insert_ignore_stock_row_gets_its_shop(db, two_shops):
    from backend.services.stock_rows import row_for
    A, _ = two_shops
    _as_shop(db, A["org"])
    loc2 = Location(org_id=A["org"], name="Annex")
    db.add(loc2)
    await db.flush()
    row = await row_for(db, A["part"], loc2.id, None, create=True)
    assert row is not None and row.org_id == A["org"]


# ── reads: another shop's child rows are invisible ──────────────────────────
async def test_cross_shop_read_of_child_rows_returns_nothing(db, two_shops):
    A, B = two_shops
    pay = Payment(kind="payment", amount=7, method="cash", order_id=B["order"])
    line = OrderLine(order_id=B["order"], line_type="part", description="B line", qty=1, unit_price=3)
    shift = CashShift(location_id=B["loc"], opened_by_id=B["emp"], opening_float=0)
    level = StockLevel(part_id=B["part"], location_id=B["loc"], qty_available=1, qty_reserved=0)
    move = StockMovement(part_id=B["part"], location_id=B["loc"], movement_type="receive", qty=1)
    db.add_all([pay, line, shift, level, move])
    await db.commit()
    ids = {Payment: pay.id, OrderLine: line.id, CashShift: shift.id, StockLevel: level.id,
           StockMovement: move.id}
    db.expunge_all()
    _as_shop(db, A["org"])
    for model, rid in ids.items():
        assert (await db.execute(select(model).where(model.id == rid))).scalar_one_or_none() is None, model
    _as_shop(db, B["org"])
    for model, rid in ids.items():
        assert (await db.execute(select(model).where(model.id == rid))).scalar_one_or_none() is not None, model


async def test_shop_payments_reads_org_id(db, two_shops):
    """DB-01: a payment linked only to a client used to be invisible to every money report."""
    from backend.services.revenue import shop_payments
    A, B = two_shops
    db.add_all([Payment(kind="payment", amount=11, method="cash", client_id=A["client"]),
                Payment(kind="payment", amount=13, method="cash", order_id=B["order"])])
    await db.commit()
    got = (await db.execute(select(Payment.amount).where(shop_payments(A["org"])))).scalars().all()
    assert sorted(got) == [11]


async def test_qbo_payment_owner_is_its_org_id(db, two_shops):
    from backend.services.qbo import _payment_in_org
    A, B = two_shops
    p = Payment(kind="payment", amount=3, method="cash", client_id=A["client"], order_id=A["order"])
    db.add(p)
    await db.commit()
    assert await _payment_in_org(db, A["org"], p)
    assert not await _payment_in_org(db, B["org"], p)


# ── migration ───────────────────────────────────────────────────────────────
def _insert(con, table: str, values: dict) -> None:
    for _cid, name, typ, notnull, default, pk in con.execute(f"PRAGMA table_info('{table}')"):
        if notnull and default is None and not pk and name not in values:
            t = (typ or "").upper()
            values[name] = 0 if any(k in t for k in ("INT", "BOOL", "NUM", "FLOAT", "REAL")) \
                else f"{name}-{uuid.uuid4().hex[:8]}"
    cols = ", ".join(values)
    con.execute(f"INSERT INTO {table} ({cols}) VALUES ({', '.join('?' * len(values))})", list(values.values()))


def _seed_legacy(path):
    con = sqlite3.connect(path)
    for org in ("oa", "ob"):
        _insert(con, "organisations", {"id": org, "name": org})
        _insert(con, "locations", {"id": f"l-{org}", "org_id": org, "name": "Main"})
        _insert(con, "employees", {"id": f"e-{org}", "org_id": org, "name": "E", "email": f"{org}@x.dev"})
        _insert(con, "clients", {"id": f"c-{org}", "org_id": org, "name": "C"})
        _insert(con, "parts", {"id": f"p-{org}", "org_id": org, "name": "P"})
        _insert(con, "orders", {"id": f"o-{org}", "org_id": org, "location_id": f"l-{org}",
                                "order_number": f"N-{org}"})
    _insert(con, "payments", {"id": "pay-order", "order_id": "o-oa", "amount": 1})
    _insert(con, "payments", {"id": "pay-client", "client_id": "c-ob", "amount": 1})
    _insert(con, "order_lines", {"id": "ol", "order_id": "o-ob"})
    _insert(con, "cash_shifts", {"id": "cs", "location_id": "l-oa", "opened_by_id": "e-oa"})
    _insert(con, "payments", {"id": "pay-shift", "shift_id": "cs", "amount": 1})
    _insert(con, "stock_levels", {"id": "sl", "part_id": "p-ob", "location_id": "l-ob"})
    _insert(con, "shifts", {"id": "sh", "employee_id": "e-ob", "location_id": "l-ob"})
    con.commit()
    return con


def test_migration_backfills_every_child_from_its_parent(tmp_path):
    from backend.tests.test_tk01_migration import _alembic
    path = tmp_path / "corg.db"
    url = f"sqlite+aiosqlite:///{path}"
    _alembic(url, "upgrade", "srch01")
    _seed_legacy(path).close()
    _alembic(url, "upgrade", "corg01")
    con = sqlite3.connect(path)
    got = {r[0]: r[1] for r in con.execute("SELECT id, org_id FROM payments")}
    assert got == {"pay-order": "oa", "pay-client": "ob", "pay-shift": "oa"}
    assert con.execute("SELECT org_id FROM order_lines").fetchone() == ("ob",)
    assert con.execute("SELECT org_id FROM stock_levels").fetchone() == ("ob",)
    assert con.execute("SELECT org_id FROM shifts").fetchone() == ("ob",)
    cols = {r[1]: r[3] for r in con.execute("PRAGMA table_info('payments')")}
    assert cols["org_id"] == 1                                        # NOT NULL
    idx = {r[1] for r in con.execute("PRAGMA index_list('payments')")}
    assert {"ix_payments_org_id_paid_at", "ix_payments_shift_id"} <= idx
    con.close()
    _alembic(url, "downgrade", "srch01")
    con = sqlite3.connect(path)
    assert "org_id" not in {r[1] for r in con.execute("PRAGMA table_info('payments')")}
    con.close()


def test_migration_fails_loudly_on_a_payment_with_no_shop(tmp_path):
    import os
    import subprocess
    import sys

    from backend.tests.test_tk01_migration import REPO, _alembic
    path = tmp_path / "orphan.db"
    url = f"sqlite+aiosqlite:///{path}"
    _alembic(url, "upgrade", "srch01")
    con = _seed_legacy(path)
    _insert(con, "payments", {"id": "pay-orphan", "amount": 1})
    con.commit()
    con.close()
    env = dict(os.environ, ENVIRONMENT="test", SEED_DEMO="false", DATABASE_URL=url)
    env.pop("TEST_DATABASE_URL", None)
    r = subprocess.run([sys.executable, "-m", "alembic", "-c", "backend/alembic.ini", "upgrade", "corg01"],
                       cwd=REPO, env=env, capture_output=True, text=True, timeout=300)
    assert r.returncode != 0
    assert "payments" in r.stderr and "no shop" in r.stderr
    con = sqlite3.connect(path)
    assert "org_id" not in {r[1] for r in con.execute("PRAGMA table_info('payments')")}   # rolled back
    con.close()
