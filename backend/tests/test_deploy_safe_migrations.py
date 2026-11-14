"""Deploy-safe migrations (re-review of #219, #221, #222): corg01, w2b01, dec08 and corg02 on real data.

SQLite (the main CI job):
- corg01 refuses rows whose parent links name two different shops, with a count and no row data.
- w2b01 writes one audit_log row per gift card, coupon or document number it renames.
- dec08 refuses out-of-range and infinite values with a per-column count, before changing anything,
  and keeps the partial/expression unique indexes on stock_levels and parts.
- dec08 alters each table once (one ALTER TABLE with every column), not once per column.

Postgres (the backend-postgres CI job; skipped on SQLite):
- corg01's backfill and NOT NULL work per table, and a row inserted the way code from before #219
  inserts it (no org_id) takes its shop from its parent, both right after corg01 and at head, so
  the old containers keep working during the deploy and after an image rollback.
- dec08's pre-flight also catches NaN and +-Infinity.
"""
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


def _alembic(url: str, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, ENVIRONMENT="test", SEED_DEMO="false", DATABASE_URL=url)
    env.pop("TEST_DATABASE_URL", None)
    return subprocess.run([sys.executable, "-m", "alembic", "-c", "backend/alembic.ini", *args],
                          cwd=REPO, env=env, capture_output=True, text=True, timeout=600)


def _ok(url: str, *args: str) -> None:
    r = _alembic(url, *args)
    assert r.returncode == 0, f"alembic {args} failed:\n{r.stderr[-3000:]}"


def _module(name: str):
    spec = importlib.util.spec_from_file_location(f"_mig_{name}", VERSIONS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _insert(con, table: str, values: dict) -> None:
    """SQLite insert with every other NOT NULL column (no default) filled with a neutral value."""
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
    _insert(con, "orders", {"id": "ordA", "org_id": "orgA", "client_id": "cl-orgA", "location_id": "loc-orgA"})


# ── corg01 N4: parent links that disagree on the shop ───────────────────────────────────────────

def test_corg01_refuses_rows_whose_links_name_two_shops(tmp_path):
    path = tmp_path / "corg01.db"
    url = f"sqlite+aiosqlite:///{path}"
    _ok(url, "upgrade", "sso01")
    con = sqlite3.connect(path)
    _two_shops(con)
    # job is shop A's, client is shop B's: COALESCE would silently pick A
    _insert(con, "payments", {"id": "pay-mixed", "order_id": "ordA", "client_id": "cl-orgB",
                              "method": "cash", "amount": 10})
    _insert(con, "payments", {"id": "pay-fine", "order_id": "ordA", "client_id": "cl-orgA",
                              "method": "cash", "amount": 5})
    con.commit()
    con.close()

    r = _alembic(url, "upgrade", "corg01")
    assert r.returncode != 0, "corg01 must refuse a payment whose job and client are in different shops"
    assert "payments: 1 row(s)" in r.stderr
    assert "different shops" in r.stderr
    for secret in ("pay-mixed", "cl-orgB", PII):      # counts only: no ids, no names
        assert secret not in r.stderr

    con = sqlite3.connect(path)
    assert "org_id" not in {c[1] for c in con.execute("PRAGMA table_info('payments')")}   # nothing changed
    con.execute("UPDATE payments SET client_id = 'cl-orgA' WHERE id = 'pay-mixed'")
    con.commit()
    con.close()
    _ok(url, "upgrade", "corg01")
    con = sqlite3.connect(path)
    assert dict(con.execute("SELECT id, org_id FROM payments")) == {"pay-mixed": "orgA", "pay-fine": "orgA"}
    con.close()


# ── w2b01 N2: renamed codes leave an audit trail ────────────────────────────────────────────────

def test_w2b01_writes_an_audit_row_per_renamed_code(tmp_path):
    path = tmp_path / "w2b01.db"
    url = f"sqlite+aiosqlite:///{path}"
    _ok(url, "upgrade", "pv01")
    con = sqlite3.connect(path)
    _insert(con, "organisations", {"id": "org", "name": "Shop"})
    _insert(con, "locations", {"id": "loc", "org_id": "org", "name": "Main"})
    for i, ts in enumerate(("2026-01-01 10:00:00", "2026-02-01 10:00:00")):
        _insert(con, "gift_cards", {"id": f"gc{i}", "org_id": "org", "code": "GC-ABC123", "balance": 5,
                                    "initial_value": 5, "created_at": ts})
        _insert(con, "coupons", {"id": f"cp{i}", "org_id": "org", "code": "SAVE10", "kind": "pct", "value": 10,
                                 "created_at": ts})
        _insert(con, "sales", {"id": f"s{i}", "org_id": "org", "location_id": "loc", "sale_number": "S-00042",
                               "created_at": ts})
    con.commit()
    con.close()

    _ok(url, "upgrade", "w2b01")
    con = sqlite3.connect(path)
    assert con.execute("SELECT code FROM gift_cards WHERE id = 'gc1'").fetchone()[0] == "GC-ABC123-2"
    rows = {(r[0], r[1]): r for r in con.execute(
        "SELECT entity_type, entity_id, org_id, action, diff, actor_id FROM audit_log")}
    assert set(rows) == {("gift_card", "gc1"), ("coupon", "cp1"), ("sale", "s1")}   # only the renamed ones
    gc = rows[("gift_card", "gc1")]
    assert gc[2] == "org" and gc[3] == "gift_card.code_renamed" and gc[5] is None
    assert "GC-ABC123" in gc[4] and "GC-ABC123-2" in gc[4] and "w2b01" in gc[4]
    assert rows[("sale", "s1")][3] == "sale.sale_number_renamed"
    con.close()


# ── dec08 N5: values NUMERIC can't hold are reported before anything changes ───────────────────

def test_dec08_refuses_out_of_range_and_infinite_values_with_counts(tmp_path):
    path = tmp_path / "dec08.db"
    url = f"sqlite+aiosqlite:///{path}"
    _ok(url, "upgrade", "w2b01")
    con = sqlite3.connect(path)
    _insert(con, "organisations", {"id": "org", "name": "Shop", "min_margin_pct": 12.5})
    _insert(con, "taxes", {"id": "t1", "org_id": "org", "name": "VAT", "rate": 20.0})
    _insert(con, "taxes", {"id": "t2", "org_id": "org", "name": "Bad", "rate": 1e6})             # > NUMERIC(9,4)
    _insert(con, "taxes", {"id": "t3", "org_id": "org", "name": "Edge", "rate": 99999.99996})    # rounds to 1e5
    _insert(con, "taxes", {"id": "t4", "org_id": "org", "name": "Inf", "rate": float("inf")})
    _insert(con, "employees", {"id": "e1", "org_id": "org", "name": PII, "email": "p@x.dev",
                               "commission_parts": float("-inf")})
    con.commit()
    con.close()

    r = _alembic(url, "upgrade", "dec08")
    assert r.returncode != 0, "dec08 must stop on values NUMERIC can't hold"
    assert "taxes.rate" in r.stderr and "2 out of range" in r.stderr and "1 infinite" in r.stderr
    assert "employees.commission_parts" in r.stderr
    assert "organisations.min_margin_pct" not in r.stderr          # in-range columns aren't listed
    assert PII not in r.stderr

    con = sqlite3.connect(path)
    con.execute("UPDATE taxes SET rate = 0 WHERE id IN ('t2', 't3', 't4')")
    con.execute("UPDATE employees SET commission_parts = 0")
    con.commit()
    con.close()
    _ok(url, "upgrade", "dec08")


# ── dec08 N4: SQLite keeps the expression/partial unique indexes ────────────────────────────────

def test_dec08_keeps_partial_and_expression_unique_indexes_on_sqlite(tmp_path):
    path = tmp_path / "dec08_idx.db"
    url = f"sqlite+aiosqlite:///{path}"
    _ok(url, "upgrade", "dec08")
    con = sqlite3.connect(path)
    sql = dict(con.execute("SELECT name, sql FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL"))
    con.close()
    for name in ("uq_parts_org_sku_active", "uq_parts_org_mpn_active", "uq_stock_levels_part_loc_unbinned"):
        assert name in sql, f"dec08 dropped {name}"
        assert " WHERE " in sql[name].upper(), f"{name} lost its partial predicate"


# ── dec08 N2: one rewrite per table on Postgres ─────────────────────────────────────────────────

def test_dec08_alters_each_table_once():
    dec08 = _module("dec08_exact_rates_qty")
    sql = dec08.pg_alter_sql("order_lines", dec08.COLUMNS["order_lines"])
    assert sql.count("ALTER TABLE") == 1
    assert sql.count("ALTER COLUMN") == len(dec08.COLUMNS["order_lines"]) == 5
    assert "USING round(qty::numeric, 4)" in sql


# ── Postgres: old code (no org_id) keeps working; NaN pre-flight ────────────────────────────────

def _pg_url() -> str:
    suite = make_url(_SUITE_URL)
    return suite.set(database=f"{suite.database}_dsm{uuid.uuid4().hex[:6]}").render_as_string(hide_password=False)


def _pg(url: str, *stmts: str):
    async def go():
        eng = create_async_engine(url, poolclass=NullPool)
        try:
            async with eng.begin() as conn:
                out = None
                for s in stmts:
                    out = (await conn.exec_driver_sql(s)).fetchall() if s.lstrip().upper().startswith("SELECT") \
                        else await conn.exec_driver_sql(s)
                return out
        finally:
            await eng.dispose()
    return db_isolation.run(go())


_NEUTRAL = {"integer": "0", "bigint": "0", "smallint": "0", "numeric": "0", "double precision": "0",
            "real": "0", "boolean": "false", "date": "current_date", "json": "'{}'", "jsonb": "'{}'"}


# columns w4s01 (and later) CHECK: the filler must be a value the rule allows
_VALID = {("payments", "kind"): "'payment'", ("transactions", "status"): "'active'"}


class Raw(str):
    """A literal SQL value (e.g. 'NaN'::float8), inserted as written."""


def _pg_insert(url: str, table: str, values: dict) -> str:
    """INSERT text with every other NOT NULL column (no default) filled with a neutral literal."""
    cols = _pg(url, "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = "
                    f"'{table}' AND is_nullable = 'NO' AND column_default IS NULL")
    lit = {k: ("NULL" if v is None else str(v) if isinstance(v, Raw) else f"'{v}'" if isinstance(v, str) else str(v))
           for k, v in values.items()}
    for name, typ in cols:
        if name not in lit and name != "org_id":      # old code never sends org_id
            lit[name] = _VALID.get((table, name)) or ("1" if name == "qty" else None) or (
                "now()" if typ.startswith("timestamp") else _NEUTRAL.get(typ, "'x'"))
    return f"INSERT INTO {table} ({', '.join(lit)}) VALUES ({', '.join(lit.values())})"


@pytest.mark.skipif(not ON_PG, reason="Postgres triggers and NUMERIC typmods: the backend-postgres job runs this")
def test_pg_old_code_inserts_without_org_id_and_dec08_preflight():
    url = _pg_url()
    base = make_url(_SUITE_URL).database
    db_isolation.run(db_isolation.create_postgres(url, base))
    try:
        _ok(url, "upgrade", "sso01")
        seed = []
        for org in ("orgA", "orgB"):
            seed += [_pg_insert(url, "organisations", {"id": org, "name": f"Shop {org}", "account_id": f"acct-{org}"}),
                     _pg_insert(url, "locations", {"id": f"loc-{org}", "org_id": org, "name": "Main"}),
                     _pg_insert(url, "clients", {"id": f"cl-{org}", "org_id": org, "name": PII})]
        _pg(url, *seed)
        _pg(url, _pg_insert(url, "orders", {"id": "ordA", "org_id": "orgA", "client_id": "cl-orgA",
                                            "location_id": "loc-orgA"}),
            _pg_insert(url, "payments", {"id": "pay-mixed", "order_id": "ordA", "client_id": "cl-orgB",
                                         "method": "cash", "amount": 10}),
            *[_pg_insert(url, "order_lines", {"id": f"ol{i}", "order_id": "ordA", "description": "Screen"})
              for i in range(7)])

        r = _alembic(url, "upgrade", "corg01")
        assert r.returncode != 0 and "payments: 1 row(s)" in r.stderr and PII not in r.stderr
        _pg(url, "UPDATE payments SET client_id = 'cl-orgA'")
        _ok(url, "upgrade", "corg01")
        assert _pg(url, "SELECT count(*) FROM order_lines WHERE org_id = 'orgA'")[0][0] == 7
        nullable = _pg(url, "SELECT is_nullable FROM information_schema.columns "
                            "WHERE table_name = 'order_lines' AND column_name = 'org_id'")[0][0]
        assert nullable == "NO"
        assert not _pg(url, "SELECT conname FROM pg_constraint WHERE conname LIKE 'ck_%_org_id_not_null'")

        # the old image, mid-deploy: inserts without org_id take the parent's shop
        _pg(url, _pg_insert(url, "order_lines", {"id": "ol-old", "order_id": "ordA", "description": "Battery"}))
        assert _pg(url, "SELECT org_id FROM order_lines WHERE id = 'ol-old'")[0][0] == "orgA"

        _ok(url, "upgrade", "w2b01")
        _pg(url, _pg_insert(url, "taxes", {"id": "t-nan", "org_id": "orgA", "name": "x", "rate": Raw("'NaN'")}),
            _pg_insert(url, "taxes", {"id": "t-inf", "org_id": "orgA", "name": "y", "rate": Raw("'Infinity'")}),
            _pg_insert(url, "accounts", {"id": "acc", "org_id": "orgA", "name": "Card", "fee_pct": 1e6}))
        r = _alembic(url, "upgrade", "dec08")
        assert r.returncode != 0
        assert "taxes.rate" in r.stderr and "1 NaN" in r.stderr and "1 infinite" in r.stderr
        assert "accounts.fee_pct" in r.stderr and "1 out of range" in r.stderr
        _pg(url, "UPDATE taxes SET rate = 0", "UPDATE accounts SET fee_pct = 0")
        _ok(url, "upgrade", "head")

        # the old image after a rollback (any pre-#219 tag): payments, stock rows, job lines
        _pg(url, _pg_insert(url, "payments", {"id": "pay-old", "order_id": "ordA", "method": "cash", "amount": 3}),
            _pg_insert(url, "parts", {"id": "partA", "org_id": "orgA", "name": "Screen"}),
            _pg_insert(url, "stock_movements", {"id": "mv-old", "part_id": "partA", "location_id": "loc-orgA",
                                                "movement_type": "adjust", "qty": 1}))
        got = dict(_pg(url, "SELECT id, org_id FROM payments UNION ALL SELECT id, org_id FROM stock_movements"))
        assert got["pay-old"] == "orgA" and got["mv-old"] == "orgA"
        # a row with no parent still fails: the trigger never invents a shop
        with pytest.raises(Exception, match="org_id"):
            _pg(url, _pg_insert(url, "payments", {"id": "pay-orphan", "method": "cash", "amount": 1}))
    finally:
        db_isolation.run(db_isolation.drop_postgres(url, base))
