"""dbi01 / dbi02 on real data (audit DB-03, DB-10, DB-12..DB-15, DB-17, SEC-12).

SQLite (the main CI job):
- dbi01 stops on rows that point at another shop's record, on devices/POs with no shop to take,
  and on orphaned money links, with counts only (no ids, no names), before changing anything;
- it clears orphaned nullable links, backfills devices/POs, and the rebuilt tables keep every
  expression/partial index (w2b01's `_sqlite_rebuild` pattern);
- dbi02 hashes and encrypts the existing tracking tokens, which keep working;
- both downgrade and upgrade again.

Postgres (the backend-postgres CI job; skipped on SQLite):
- every constraint dbi01 adds is VALIDATED, every index it builds is valid;
- the database refuses a cross-shop payment / stock row inserted with raw SQL;
- a device inserted the way old code does (no org_id) takes its client's shop (fill trigger);
- the two money FKs no longer cascade.
"""
import hashlib
import importlib.util
import os
import sqlite3
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from backend.tests import db_isolation

REPO = Path(__file__).resolve().parents[2]
VERSIONS = REPO / "backend" / "migrations" / "versions"
_SUITE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL", "")
ON_PG = _SUITE_URL.startswith("postgresql")
PII = "Priya Secretname"


def migration(name: str):
    spec = importlib.util.spec_from_file_location(f"_mig_{name}", VERSIONS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _alembic(url: str, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, ENVIRONMENT="test", SEED_DEMO="false", DATABASE_URL=url)
    env.pop("TEST_DATABASE_URL", None)
    return subprocess.run([sys.executable, "-m", "alembic", "-c", "backend/alembic.ini", *args],
                          cwd=REPO, env=env, capture_output=True, text=True, timeout=900)


def _ok(url: str, *args: str) -> subprocess.CompletedProcess:
    r = _alembic(url, *args)
    assert r.returncode == 0, f"alembic {args} failed:\n{r.stderr[-4000:]}"
    return r


def _insert(con, table: str, values: dict) -> None:
    for _cid, name, typ, notnull, default, pk in con.execute(f"PRAGMA table_info('{table}')"):
        if notnull and default is None and not pk and name not in values:
            t = (typ or "").upper()
            values[name] = 0 if any(k in t for k in ("INT", "BOOL", "NUM", "FLOAT", "REAL")) else ""
    con.execute(f"INSERT INTO {table} ({', '.join(values)}) VALUES ({', '.join('?' * len(values))})",
                list(values.values()))


def _two_shops(con) -> None:
    for org in ("orgA", "orgB"):
        _insert(con, "organisations", {"id": org, "name": f"Shop {org}", "account_id": f"acct-{org}"})
        _insert(con, "locations", {"id": f"loc-{org}", "org_id": org, "name": "Main"})
        _insert(con, "clients", {"id": f"cl-{org}", "org_id": org, "name": PII})
        _insert(con, "orders", {"id": f"ord-{org}", "org_id": org, "client_id": f"cl-{org}",
                                "location_id": f"loc-{org}", "order_number": "W-1", "public_token": f"tok-{org}"})


def _at(tmp_path, rev: str, name: str):
    path = tmp_path / f"{name}.db"
    url = f"sqlite+aiosqlite:///{path}"
    _ok(url, "upgrade", rev)
    return path, url


def _fk_sql(con, table: str) -> str:
    return con.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone()[0]


# ── dbi01: refuses, with counts only ────────────────────────────────────────────────────────────

def test_dbi01_stops_on_cross_shop_rows_shopless_devices_and_orphaned_money_links(tmp_path):
    path, url = _at(tmp_path, "corg02", "dbi01_refuse")
    con = sqlite3.connect(path)
    _two_shops(con)
    _insert(con, "payments", {"id": "pay-x", "org_id": "orgA", "order_id": "ord-orgA", "client_id": "cl-orgB",
                              "method": "cash", "amount": 10})                     # B's client on A's payment
    _insert(con, "devices", {"id": "dev-nowhere", "brand": PII})                    # no shop, no links
    _insert(con, "transactions", {"id": "tx-1", "org_id": "orgA", "reverses_txn_id": "tx-gone",
                                  "txn_type": "income", "amount": 1})               # a money link to nothing
    con.commit()
    con.close()

    r = _alembic(url, "upgrade", "dbi01")
    assert r.returncode != 0, "dbi01 must stop on bad rows"
    err = r.stderr
    assert "payments.client_id -> clients: 1 row(s)" in err
    assert "devices: 1 row(s)" in err
    assert "transactions.reverses_txn_id: 1 row(s)" in err
    assert "find_cross_shop_rows.py" in err
    for secret in ("pay-x", "cl-orgB", "dev-nowhere", "tx-gone", PII):
        assert secret not in err, f"{secret} leaked into the error"
    con = sqlite3.connect(path)
    assert "fk_payments_client_id_org" not in _fk_sql(con, "payments")       # nothing changed
    con.execute("UPDATE payments SET client_id = 'cl-orgA'")
    con.execute("UPDATE devices SET client_id = 'cl-orgB'")
    con.execute("UPDATE transactions SET reverses_txn_id = NULL")
    con.commit()
    con.close()
    _ok(url, "upgrade", "dbi01")
    con = sqlite3.connect(path)
    assert con.execute("SELECT org_id FROM devices WHERE id = 'dev-nowhere'").fetchone()[0] == "orgB"
    con.close()


def test_find_cross_shop_rows_lists_what_dbi01_would_stop_on(tmp_path):
    path, url = _at(tmp_path, "corg02", "script")
    con = sqlite3.connect(path)
    _two_shops(con)
    _insert(con, "payments", {"id": "pay-x", "org_id": "orgA", "order_id": "ord-orgA", "client_id": "cl-orgB",
                              "method": "cash", "amount": 10})
    con.commit()
    con.close()
    env = dict(os.environ, ENVIRONMENT="test", DATABASE_URL=url)
    env.pop("TEST_DATABASE_URL", None)
    r = subprocess.run([sys.executable, "scripts/find_cross_shop_rows.py"], cwd=REPO, env=env,
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "FOUND payments.client_id: its clients row is another shop's: 1 row(s), e.g. ['pay-x']" in r.stdout
    assert "ok    orders.client_id" in r.stdout


def test_dbi01_clears_orphaned_links_backfills_shops_and_the_keys_hold(tmp_path):
    path, url = _at(tmp_path, "corg02", "dbi01_ok")
    con = sqlite3.connect(path)
    _two_shops(con)
    _insert(con, "sales", {"id": "s1", "org_id": "orgA", "location_id": "loc-orgA", "sale_number": "S-1",
                           "coupon_id": "coupon-gone"})
    _insert(con, "devices", {"id": "dev-1", "client_id": "cl-orgA"})                    # org from its client
    _insert(con, "purchase_orders", {"id": "po-1", "location_id": "loc-orgB", "supplier_id": "sup", "po_number": "P1"})
    con.commit()
    con.close()

    r = _ok(url, "upgrade", "dbi01")
    assert "sales.coupon_id: 1 link(s)" in r.stderr
    con = sqlite3.connect(path)
    con.execute("PRAGMA foreign_keys=ON")
    assert con.execute("SELECT coupon_id FROM sales WHERE id = 's1'").fetchone()[0] is None
    assert con.execute("SELECT org_id FROM devices").fetchone()[0] == "orgA"
    assert con.execute("SELECT org_id FROM purchase_orders").fetchone()[0] == "orgB"
    notnull = {r[1]: r[3] for r in con.execute("PRAGMA table_info('devices')")}
    assert notnull["org_id"] == 1
    # the database refuses a cross-shop row now, whatever writes it
    with pytest.raises(sqlite3.IntegrityError):
        _insert(con, "payments", {"id": "p2", "org_id": "orgA", "order_id": "ord-orgA", "client_id": "cl-orgB",
                                  "method": "cash", "kind": "payment", "amount": 1})
    _insert(con, "payments", {"id": "p3", "org_id": "orgA", "order_id": "ord-orgA", "client_id": "cl-orgA",
                              "method": "cash", "kind": "payment", "amount": 1})
    con.close()


def test_dbi01_keeps_every_expression_and_partial_index_on_sqlite(tmp_path):
    path, url = _at(tmp_path, "corg02", "dbi01_idx")
    dbi01 = migration("dbi01_tenant_keys_and_indexes")
    dropped = {n for n, *_ in dbi01.DROPPED_INDEXES}
    con = sqlite3.connect(path)
    before = {n: " ".join(s.split()) for n, s in con.execute(
        "SELECT name, sql FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL") if n not in dropped}
    con.close()
    assert "uq_stock_levels_part_loc_unbinned" in before and "uq_storage_bins_name" in before
    _ok(url, "upgrade", "dbi01")
    con = sqlite3.connect(path)
    after = {n: " ".join(s.split()) for n, s in con.execute(
        "SELECT name, sql FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL")}
    con.close()
    lost = sorted(n for n in before if n not in after)
    changed = sorted(n for n in before if n in after and before[n] != after[n])
    assert not lost and not changed, f"lost {lost}, changed {changed}"
    assert not (dropped & set(after)), "the redundant indexes are gone"


# ── dbi02: existing tracking links keep working ─────────────────────────────────────────────────

def test_dbi02_hashes_existing_tokens_and_keeps_the_plaintext_for_the_old_image(tmp_path):
    path, url = _at(tmp_path, "dbi01", "dbi02")
    con = sqlite3.connect(path)
    _two_shops(con)
    con.commit()
    con.close()
    _ok(url, "upgrade", "dbi02")
    con = sqlite3.connect(path)
    rows = {r[0]: r[1:] for r in con.execute(
        "SELECT id, public_token, public_token_hash, public_token_enc FROM orders")}
    con.close()
    plain, digest, enc = rows["ord-orgA"]
    assert plain == "tok-orgA"                       # the previous image still opens it
    assert digest == hashlib.sha256(b"tok-orgA").hexdigest()
    assert enc == "tok-orgA"                         # no key in tests: crypto passes it through


def test_dbi01_dbi02_downgrade_and_upgrade_again(tmp_path):
    path, url = _at(tmp_path, "head", "roundtrip")
    _ok(url, "downgrade", "corg02")
    con = sqlite3.connect(path)
    assert "fk_payments_client_id_org" not in _fk_sql(con, "payments")
    assert "CASCADE" in _fk_sql(con, "credit_note_lines")
    con.close()
    _ok(url, "upgrade", "head")


# ── Postgres ────────────────────────────────────────────────────────────────────────────────────

def _pg(url: str, *stmts: str):
    async def go():
        eng = create_async_engine(url, poolclass=NullPool)
        try:
            async with eng.begin() as conn:
                out = None
                for s in stmts:
                    res = await conn.exec_driver_sql(s)
                    out = res.fetchall() if s.lstrip().upper().startswith("SELECT") else res
                return out
        finally:
            await eng.dispose()
    return db_isolation.run(go())


def _pg_insert(url: str, table: str, values: dict, old_code: bool = False) -> str:
    cols = _pg(url, "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = "
                    f"'{table}' AND is_nullable = 'NO' AND column_default IS NULL")
    neutral = {"integer": "0", "bigint": "0", "smallint": "0", "numeric": "0", "double precision": "0",
               "real": "0", "boolean": "false", "date": "current_date", "json": "'{}'", "jsonb": "'{}'"}
    lit = {k: "NULL" if v is None else f"'{v}'" if isinstance(v, str) else str(v) for k, v in values.items()}
    for name, typ in cols:
        if name not in lit and not (old_code and name == "org_id"):
            lit[name] = "now()" if typ.startswith("timestamp") else neutral.get(typ, "'x'")
    return f"INSERT INTO {table} ({', '.join(lit)}) VALUES ({', '.join(lit.values())})"


@pytest.mark.skipif(not ON_PG, reason="NOT VALID / VALIDATE, CONCURRENTLY and triggers: the backend-postgres job")
def test_pg_dbi01_keys_are_validated_and_hold():
    suite = make_url(_SUITE_URL)
    url = suite.set(database=f"{suite.database}_dbi{uuid.uuid4().hex[:6]}").render_as_string(hide_password=False)
    db_isolation.run(db_isolation.create_postgres(url, suite.database))
    try:
        _ok(url, "upgrade", "corg02")
        seed = []
        for org in ("orgA", "orgB"):
            seed += [_pg_insert(url, "organisations", {"id": org, "name": org, "account_id": f"acct-{org}"}),
                     _pg_insert(url, "locations", {"id": f"loc-{org}", "org_id": org, "name": "Main"}),
                     _pg_insert(url, "clients", {"id": f"cl-{org}", "org_id": org, "name": PII}),
                     _pg_insert(url, "parts", {"id": f"part-{org}", "org_id": org, "name": "Screen"}),
                     _pg_insert(url, "employees", {"id": f"emp-{org}", "org_id": org, "name": "Staff",
                                                   "email": f"staff-{org}@x.dev"})]
        _pg(url, *seed)
        _pg(url, _pg_insert(url, "orders", {"id": "ordA", "org_id": "orgA", "client_id": "cl-orgA",
                                            "location_id": "loc-orgA", "public_token": "tok-A"}),
            _pg_insert(url, "devices", {"id": "dev-old", "client_id": "cl-orgA"}, old_code=True),
            _pg_insert(url, "sales", {"id": "s1", "org_id": "orgA", "location_id": "loc-orgA",
                                      "sale_number": "S-1", "coupon_id": "gone",
                                      "seller_id": "emp-orgA"}))
        _ok(url, "upgrade", "head")

        dbi01 = migration("dbi01_tenant_keys_and_indexes")
        names = ([dbi01.tenant_fk_name(c, col) for c, col, _p in dbi01.TENANT_FKS]
                 + [dbi01.soft_fk_name(t, col, ref) for t, col, ref, *_ in dbi01.SOFT_FKS])
        got = dict(_pg(url, "SELECT conname, convalidated FROM pg_constraint WHERE contype = 'f'"))
        assert not [n for n in names if n not in got], "missing FKs"
        assert all(got[n] for n in names), "every FK is VALIDATED"
        assert not _pg(url, "SELECT indexrelid::regclass FROM pg_index WHERE NOT indisvalid")
        assert _pg(url, "SELECT org_id FROM devices WHERE id = 'dev-old'")[0][0] == "orgA"
        assert _pg(url, "SELECT coupon_id FROM sales WHERE id = 's1'")[0][0] is None
        # DB-12: the two money FKs no longer cascade ('a' = NO ACTION)
        rules = dict(_pg(url, "SELECT conrelid::regclass::text, confdeltype::text FROM pg_constraint WHERE contype = 'f' "
                              "AND conrelid::regclass::text IN ('credit_note_lines', 'serial_events') "
                              "AND confrelid::regclass::text IN ('credit_notes', 'serialized_items')"))
        assert rules == {"credit_note_lines": "a", "serial_events": "a"}
        # the old image inserting a device without org_id: the trigger gives it its client's shop
        _pg(url, _pg_insert(url, "devices", {"id": "dev-old2", "client_id": "cl-orgB"}, old_code=True))
        assert _pg(url, "SELECT org_id FROM devices WHERE id = 'dev-old2'")[0][0] == "orgB"
        # the database itself refuses cross-shop rows
        with pytest.raises(Exception, match="fk_payments_client_id_org"):
            _pg(url, _pg_insert(url, "payments", {"id": "p-x", "org_id": "orgA", "order_id": "ordA",
                                                  "client_id": "cl-orgB", "method": "cash", "amount": 1}))
        with pytest.raises(Exception, match="fk_stock_movements_part_id_org"):
            _pg(url, _pg_insert(url, "stock_movements", {"id": "m-x", "org_id": "orgA", "part_id": "part-orgB",
                                                         "location_id": "loc-orgA", "movement_type": "adjust",
                                                         "qty": 1}))
        # SEC-12: the existing token was hashed, the plaintext kept for the previous image
        h = _pg(url, "SELECT public_token_hash FROM orders WHERE id = 'ordA'")[0][0]
        assert h == hashlib.sha256(b"tok-A").hexdigest()
        # and back down, and up again
        _ok(url, "downgrade", "corg02")
        _ok(url, "upgrade", "head")
    finally:
        db_isolation.run(db_isolation.drop_postgres(url, suite.database))
