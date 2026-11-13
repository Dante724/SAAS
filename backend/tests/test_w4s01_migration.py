"""w4s01 on real data: redemption line roles (FIN-03), money CHECKs (DB-07), one open shift per
employee, membership-asset uniqueness (DB-04), the per-bin default flag (DB-16 release 1).

SQLite (main CI job): the pre-flight stops with counts only and changes nothing; the repairs and
backfills; the rules hold afterwards; the triggers keep the copies right; every expression and
partial index survives; downgrade and upgrade again.
Postgres (backend-postgres job): every CHECK VALIDATED, every index valid, the old image's line gets
its role from the bridge trigger, and the rules hold.
The models' trigger SQL is the migration's (test DBs are built from the models).
"""
import sqlite3
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.engine import make_url

from backend.tests import db_isolation
from backend.tests.test_dbi_migrations import (
    _SUITE_URL,
    ON_PG,
    PII,
    _alembic,
    _at,
    _insert,
    _ok,
    _pg,
    _pg_insert,
    _two_shops,
    migration,
)

W4 = "w4s01_line_roles_money_checks_one_open_shift"
PREV = "w4i01"     # the revision before w4s01


def _seed_rules_data(con):
    _two_shops(con)
    _insert(con, "employees", {"id": "emp-1", "org_id": "orgA", "name": PII, "email": "e1@x.dev"})
    _insert(con, "membership_plans", {"id": "plan-1", "org_id": "orgA", "name": "Care"})
    _insert(con, "devices", {"id": "dev-1", "org_id": "orgA", "client_id": "cl-orgA"})
    _insert(con, "warehouses", {"id": "wh-1", "org_id": "orgA", "name": "Main"})
    for b in ("bin-a", "bin-b"):
        _insert(con, "storage_bins", {"id": b, "org_id": "orgA", "location_id": "loc-orgA", "warehouse_id": "wh-1",
                                      "name": b, "active": 1})


def test_w4s01_stops_on_rows_it_cannot_repair_with_counts_only(tmp_path):
    path, url = _at(tmp_path, PREV, "w4_refuse")
    con = sqlite3.connect(path)
    _two_shops(con)
    _insert(con, "payments", {"id": "pay-neg", "org_id": "orgA", "order_id": "ord-orgA", "method": "cash",
                              "kind": "payment", "amount": -12.5})
    _insert(con, "payments", {"id": "pay-odd", "org_id": "orgA", "order_id": "ord-orgA", "method": "cash",
                              "kind": "chargeback", "amount": 1})
    _insert(con, "transactions", {"id": "tx-odd", "org_id": "orgA", "account_id": "a", "txn_type": "income",
                                  "amount": 1, "status": "weird"})
    _insert(con, "sales", {"id": "s1", "org_id": "orgA", "location_id": "loc-orgA", "sale_number": "S-1"})
    _insert(con, "sale_lines", {"id": "sl-0", "org_id": "orgA", "sale_id": "s1", "description": PII, "qty": 0})
    con.commit()
    con.close()
    r = _alembic(url, "upgrade", "w4s01")
    assert r.returncode != 0, "w4s01 must stop"
    err = r.stderr
    assert "payments: 1 payment row(s) with a negative amount" in err
    assert "payments.kind = 'chargeback' on 1 row(s)" in err
    assert "transactions.status = 'weird' on 1 row(s)" in err
    assert "sale_lines: 1 line(s) with qty 0" in err
    for secret in ("pay-neg", "pay-odd", "tx-odd", "sl-0", PII, "12.5"):
        assert secret not in err, f"{secret} leaked into the error"
    con = sqlite3.connect(path)
    assert "line_role" not in {r[1] for r in con.execute("PRAGMA table_info('order_lines')")}, "nothing changed"
    con.close()


def test_w4s01_repairs_backfills_and_the_rules_hold(tmp_path):
    path, url = _at(tmp_path, PREV, "w4_ok")
    con = sqlite3.connect(path)
    _seed_rules_data(con)
    _insert(con, "gift_cards", {"id": "gc-1", "org_id": "orgA", "code": "GC-ABC", "initial_value": 50, "balance": 0})
    lines = {"l-item": ("labour", "service", "Repair", 100), "l-gift": ("fee", "custom", "Gift card GC-ABC", -50),
             "l-coupon": ("fee", "custom", "Coupon SAVE", -5), "l-pts": ("fee", "custom", "Loyalty points (10)", -10),
             "l-disc": ("fee", "custom", "Goodwill", -3)}
    for lid, (lt, kind, desc, total) in lines.items():
        _insert(con, "order_lines", {"id": lid, "org_id": "orgA", "order_id": "ord-orgA", "line_type": lt,
                                     "kind": kind, "description": desc, "unit_price": total, "total": total})
    _insert(con, "payments", {"id": "ref-neg", "org_id": "orgA", "order_id": "ord-orgA", "method": "cash",
                              "kind": "refund", "amount": -7})
    for sid, start in (("sh-old", "2026-11-01 08:00:00"), ("sh-new", "2026-11-02 08:00:00")):
        _insert(con, "shifts", {"id": sid, "org_id": "orgA", "employee_id": "emp-1", "location_id": "loc-orgA",
                                "started_at": start})
    _insert(con, "shifts", {"id": "sh-capped", "org_id": "orgA", "employee_id": "emp-1", "location_id": "loc-orgA",
                            "started_at": "2026-10-01 08:00:00", "ended_at": "2026-10-02 00:00:00"})
    _insert(con, "audit_log", {"id": "au-1", "org_id": "orgA", "action": "shift.auto_capped", "entity_type": "shift",
                               "entity_id": "sh-capped", "created_at": "2026-10-02 00:00:00"})
    _insert(con, "memberships", {"id": "m-1", "org_id": "orgA", "plan_id": "plan-1", "client_id": "cl-orgA",
                                 "status": "active", "billing": "yearly"})
    _insert(con, "membership_assets", {"id": "ma-1", "org_id": "orgA", "membership_id": "m-1", "device_id": "dev-1"})
    con.execute("UPDATE warehouses SET default_bin_id = 'bin-b'")
    con.commit()
    con.close()

    _ok(url, "upgrade", "w4s01")
    con = sqlite3.connect(path)
    roles = dict(con.execute("SELECT id, line_role FROM order_lines"))
    assert roles == {"l-item": "item", "l-gift": "giftcard", "l-coupon": "coupon", "l-pts": "loyalty",
                     "l-disc": "item"}
    assert con.execute("SELECT ref_id FROM order_lines WHERE id = 'l-gift'").fetchone()[0] == "gc-1"
    assert con.execute("SELECT amount FROM payments WHERE id = 'ref-neg'").fetchone()[0] == 7
    shifts = {r[0]: r[1:] for r in con.execute("SELECT id, ended_at IS NULL, needs_review, hours_worked FROM shifts")}
    assert shifts["sh-new"][:2] == (1, 0)
    assert shifts["sh-old"] == (0, 1, 0), "the older duplicate closes at its start, 0 hours, for review"
    assert shifts["sh-capped"][1] == 1, "the cap's flag moved from the audit log onto the shift"
    assert con.execute("SELECT plan_id, live FROM membership_assets").fetchone() == ("plan-1", 1)
    assert dict(con.execute("SELECT id, is_default FROM storage_bins")) == {"bin-a": 0, "bin-b": 1}

    # the rules hold afterwards, whatever writes
    with pytest.raises(sqlite3.IntegrityError):
        _insert(con, "payments", {"id": "p-neg", "org_id": "orgA", "order_id": "ord-orgA", "method": "cash",
                                  "kind": "payment", "amount": -1})
    with pytest.raises(sqlite3.IntegrityError):
        _insert(con, "shifts", {"id": "sh-2nd", "org_id": "orgA", "employee_id": "emp-1", "location_id": "loc-orgA"})
    with pytest.raises(sqlite3.IntegrityError):
        _insert(con, "order_lines", {"id": "l-bad", "org_id": "orgA", "order_id": "ord-orgA", "line_type": "fee",
                                     "description": "x", "line_role": "voucher"})
    # DB-04: a second live membership of the plan covering the same asset (a fleet plan's asset row)
    _insert(con, "memberships", {"id": "m-2", "org_id": "orgA", "plan_id": "plan-1", "client_id": "cl-orgA",
                                 "status": "active", "billing": "yearly"})
    with pytest.raises(sqlite3.IntegrityError):
        _insert(con, "membership_assets", {"id": "ma-2", "org_id": "orgA", "membership_id": "m-2",
                                           "device_id": "dev-1"})
    con.execute("UPDATE memberships SET status = 'cancelled' WHERE id = 'm-1'")
    assert con.execute("SELECT live FROM membership_assets WHERE id = 'ma-1'").fetchone()[0] == 0
    _insert(con, "membership_assets", {"id": "ma-2", "org_id": "orgA", "membership_id": "m-2", "device_id": "dev-1"})
    assert con.execute("SELECT plan_id, live FROM membership_assets WHERE id = 'ma-2'").fetchone() == ("plan-1", 1)
    # DB-16: the flag follows warehouses.default_bin_id
    con.execute("UPDATE warehouses SET default_bin_id = 'bin-a'")
    assert dict(con.execute("SELECT id, is_default FROM storage_bins")) == {"bin-a": 1, "bin-b": 0}
    con.execute("UPDATE warehouses SET default_bin_id = NULL")
    assert dict(con.execute("SELECT id, is_default FROM storage_bins")) == {"bin-a": 0, "bin-b": 0}
    con.close()


def test_w4s01_keeps_every_expression_and_partial_index_and_round_trips(tmp_path):
    path, url = _at(tmp_path, PREV, "w4_idx")
    con = sqlite3.connect(path)
    before = {n: " ".join(s.split()) for n, s in con.execute(
        "SELECT name, sql FROM sqlite_master WHERE type IN ('index', 'trigger') AND sql IS NOT NULL")}
    con.close()
    _ok(url, "upgrade", "w4s01")
    con = sqlite3.connect(path)
    after = {n: " ".join(s.split()) for n, s in con.execute(
        "SELECT name, sql FROM sqlite_master WHERE type IN ('index', 'trigger') AND sql IS NOT NULL")}
    con.close()
    lost = sorted(n for n in before if n not in after)
    changed = sorted(n for n in before if n in after and before[n] != after[n])
    assert not lost and not changed, f"lost {lost}, changed {changed}"
    for name in ("uq_shifts_open_employee", "uq_membership_assets_live_device_plan",
                 "uq_storage_bins_default_warehouse", "memberships_sync_assets", "warehouses_sync_default_bin"):
        assert name in after
    _ok(url, "downgrade", PREV)
    con = sqlite3.connect(path)
    back = {n: " ".join(s.split()) for n, s in con.execute(
        "SELECT name, sql FROM sqlite_master WHERE type IN ('index', 'trigger') AND sql IS NOT NULL")}
    assert "line_role" not in {r[1] for r in con.execute("PRAGMA table_info('order_lines')")}
    con.close()
    assert back == before, "the downgrade puts the schema's indexes and triggers back exactly"
    _ok(url, "upgrade", "head")


def test_models_and_migration_create_the_same_triggers():
    from backend import models
    w4 = migration(W4)
    assert models.ORDER_LINE_PG_TRIGGERS == w4.PG_TRIGGERS
    for dialect in ("sqlite", "postgresql"):
        assert models.MEMBERSHIP_ASSET_TRIGGERS[dialect] == w4.membership_trigger_sql(dialect)
        assert models.STORAGE_BIN_TRIGGERS[dialect] == w4.bin_trigger_sql(dialect)


async def test_the_app_schema_keeps_the_bin_flag_and_open_shift_rule(db, tenant, client):
    """The test database is built from the models: the same triggers and indexes are there."""
    from sqlalchemy.exc import IntegrityError

    from backend.models import Employee, Location, Shift, StorageBin, Warehouse
    emp = (await db.execute(select(Employee))).scalars().first()
    loc = (await db.execute(select(Location).where(Location.org_id == emp.org_id))).scalars().first()
    w = Warehouse(org_id=emp.org_id, name="W", location_id=loc.id)
    db.add(w)
    await db.flush()
    a = StorageBin(org_id=emp.org_id, location_id=loc.id, warehouse_id=w.id, name="A1")
    b = StorageBin(org_id=emp.org_id, location_id=loc.id, warehouse_id=w.id, name="B1")
    db.add_all([a, b])
    await db.flush()
    w.default_bin_id = b.id
    await db.flush()
    flags = dict((await db.execute(select(StorageBin.name, StorageBin.is_default).where(
        StorageBin.warehouse_id == w.id).execution_options(populate_existing=True))).all())
    assert flags == {"A1": False, "B1": True}
    db.add(Shift(org_id=emp.org_id, employee_id=emp.id, location_id=loc.id))
    await db.flush()
    db.add(Shift(org_id=emp.org_id, employee_id=emp.id, location_id=loc.id))
    with pytest.raises(IntegrityError):
        await db.flush()
    await db.rollback()


# ── Postgres ────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.skipif(not ON_PG, reason="NOT VALID / VALIDATE, CONCURRENTLY and the bridge trigger: backend-postgres job")
def test_pg_w4s01_checks_validated_indexes_valid_and_old_image_lines_get_a_role():
    suite = make_url(_SUITE_URL)
    url = suite.set(database=f"{suite.database}_w4{uuid.uuid4().hex[:6]}").render_as_string(hide_password=False)
    db_isolation.run(db_isolation.create_postgres(url, suite.database))
    try:
        _ok(url, "upgrade", PREV)
        seed = []
        for org in ("orgA",):
            seed += [_pg_insert(url, "organisations", {"id": org, "name": org, "account_id": f"acct-{org}"}),
                     _pg_insert(url, "locations", {"id": f"loc-{org}", "org_id": org, "name": "Main"}),
                     _pg_insert(url, "clients", {"id": f"cl-{org}", "org_id": org, "name": PII})]
        _pg(url, *seed)
        _pg(url, _pg_insert(url, "orders", {"id": "ordA", "org_id": "orgA", "client_id": "cl-orgA",
                                            "location_id": "loc-orgA", "public_token": "tok-A"}),
            _pg_insert(url, "gift_cards", {"id": "gc-1", "org_id": "orgA", "code": "GC-ABC", "initial_value": 50,
                                           "balance": 0}),
            _pg_insert(url, "order_lines", {"id": "l-gift", "org_id": "orgA", "order_id": "ordA", "line_type": "fee",
                                            "kind": "custom", "description": "Gift card GC-ABC", "unit_price": -50,
                                            "total": -50}),
            _pg_insert(url, "payments", {"id": "ref-neg", "org_id": "orgA", "order_id": "ordA", "method": "cash",
                                         "kind": "refund", "amount": -7}))
        _ok(url, "upgrade", "head")
        w4 = migration(W4)
        got = dict(_pg(url, "SELECT conname, convalidated FROM pg_constraint WHERE contype = 'c'"))
        names = [n for _t, n, _e in w4.CHECKS]
        assert not [n for n in names if n not in got], "missing CHECKs"
        assert all(got[n] for n in names), "every CHECK is VALIDATED"
        assert not _pg(url, "SELECT indexrelid::regclass FROM pg_index WHERE NOT indisvalid")
        assert _pg(url, "SELECT line_role, ref_id FROM order_lines WHERE id = 'l-gift'")[0] == ("giftcard", "gc-1")
        assert float(_pg(url, "SELECT amount FROM payments WHERE id = 'ref-neg'")[0][0]) == 7
        # the previous image inserts a redemption line without line_role: the bridge trigger gives it one
        _pg(url, _pg_insert(url, "order_lines", {"id": "l-old", "org_id": "orgA", "order_id": "ordA",
                                                 "line_type": "fee", "kind": "custom", "description": "Coupon X",
                                                 "unit_price": -5, "total": -5, "line_role": None}))
        assert _pg(url, "SELECT line_role FROM order_lines WHERE id = 'l-old'")[0][0] == "coupon"
        with pytest.raises(Exception, match="ck_payments_amount_nonneg"):
            _pg(url, _pg_insert(url, "payments", {"id": "p-neg", "org_id": "orgA", "order_id": "ordA",
                                                  "method": "cash", "kind": "payment", "amount": -1}))
        _ok(url, "downgrade", PREV)
        _ok(url, "upgrade", "head")
    finally:
        db_isolation.run(db_isolation.drop_postgres(url, suite.database))
