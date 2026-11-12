"""144eo — tests that run against a database built by the MIGRATIONS (like production), not by
create_all (like the rest of the suite). The whole suite builds its schema from the models, so any
place where the migrations disagree with the models was invisible to it — that's how a fired
notification rule became undeletable on migrated databases. These tests close that blind spot.

1. Hard drift gate: migrated schema must match the models, except an explicit, shrinking allowlist
   of known differences. Any NEW mismatch (missing table/column/index, length, FK, ondelete, or a
   nullable change in the dangerous direction) fails CI.
2. Behaviour on the migrated schema: deleting a rule that has fired keeps its logs.
3. API validation for document template page_size.
"""
import os
import sqlite3
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from backend.db.database import Base
from backend.models import all_models  # noqa: F401 — registers every table
from backend.tests import db_isolation

REPO = Path(__file__).resolve().parents[2]
# The migrated database follows the suite's backend: Postgres when TEST_DATABASE_URL points at one
# (10+ migrations branch on the dialect, so the SQLite gate alone misses Postgres-only drift),
# SQLite otherwise. conftest already made this URL unique to the process; the migrated database is
# derived from it with its own suffix, so it never collides with the suite's create_all database.
_SUITE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL", "")
MIGRATED_ON_PG = _SUITE_URL.startswith("postgresql")

# ── Known, reviewed differences (tracked debt). The gate fails on anything NOT listed here, and
# also fails if a listed item is no longer a difference — so this list can only shrink. ──
# Foreign keys the models declare but no migration creates. Empty since fk144ep01 added the last 11 —
# keep it that way: a new model FK needs a migration (with an orphan pre-check, see fk144ep01).
KNOWN_MISSING_FKS: set[tuple[str, str]] = set()
# Equivalent types: unbounded VARCHAR vs TEXT store the same thing on SQLite and Postgres.
KNOWN_TYPE_DIFFS = {("clients", "billing_address")}


def _upgrade_head(url: str) -> None:
    env = dict(os.environ, ENVIRONMENT="test", SEED_DEMO="false", DATABASE_URL=url)
    env.pop("TEST_DATABASE_URL", None)
    r = subprocess.run([sys.executable, "-m", "alembic", "-c", "backend/alembic.ini", "upgrade", "head"],
                       cwd=REPO, env=env, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, f"migrations failed:\n{r.stderr[-3000:]}"


@pytest.fixture(scope="module")
def migrated_db(tmp_path_factory):
    """URL of a fresh database built ONLY by `alembic upgrade head`: a new Postgres database on the
    suite's server when the suite runs on Postgres, else a SQLite file."""
    if not MIGRATED_ON_PG:
        path = tmp_path_factory.mktemp("migrated") / "migrated.db"
        _upgrade_head(f"sqlite+aiosqlite:///{path}")
        yield path
        return
    suite = make_url(_SUITE_URL)
    # render_as_string(hide_password=False): str() of a URL masks the password as "***", which only
    # works on a server that doesn't check it (CI's Postgres does: login failed for "repairos").
    url = suite.set(database=f"{suite.database}_mig{uuid.uuid4().hex[:6]}").render_as_string(hide_password=False)
    db_isolation.run(db_isolation.create_postgres(url, suite.database))
    try:
        _upgrade_head(url)
        yield url
    finally:
        db_isolation.run(db_isolation.drop_postgres(url, suite.database))


def _drift(migrated):
    url = migrated if MIGRATED_ON_PG else f"sqlite+aiosqlite:///{migrated}"

    async def compare():
        eng = create_async_engine(url, poolclass=NullPool)
        try:
            async with eng.connect() as conn:
                return await conn.run_sync(
                    lambda sync_conn: compare_metadata(MigrationContext.configure(sync_conn), Base.metadata))
        finally:
            await eng.dispose()
    diff = db_isolation.run(compare())
    flat = []
    for d in diff:
        flat.extend(d if isinstance(d, list) else [d])
    return flat


def test_migrated_schema_matches_models(migrated_db):
    unexpected, seen_fks, seen_types = [], set(), set()
    for op in _drift(migrated_db):
        kind = op[0]
        if kind == "modify_nullable":
            _, _, table, col, _, db_nullable, model_nullable = op
            if db_nullable is True and model_nullable is False:
                continue  # DB looser than the model: the ORM always sends a value, nothing can fail
            unexpected.append(f"{table}.{col}: NOT NULL in DB but nullable in model (inserts can fail)")
        elif kind == "add_fk":
            fk = op[1]
            key = (fk.table.name, fk.columns[0].name)
            if key in KNOWN_MISSING_FKS:
                seen_fks.add(key)
                continue
            unexpected.append(f"FK in model but not in migrations: {key[0]}.{key[1]}")
        elif kind == "modify_type":
            key = (op[2], op[3])
            if key in KNOWN_TYPE_DIFFS:
                seen_types.add(key)
                continue
            unexpected.append(f"type differs: {key[0]}.{key[1]} db={op[5]} model={op[6]}")
        else:
            unexpected.append(f"{kind}: {op[1:]}"[:300])
    stale = sorted(KNOWN_MISSING_FKS - seen_fks) + sorted(KNOWN_TYPE_DIFFS - seen_types)
    assert not unexpected, (
        "The migrations no longer build the schema the models describe — add a migration:\n  "
        + "\n  ".join(unexpected))
    assert not stale, f"Fixed — remove from the allowlist in this file: {stale}"


def test_every_org_id_column_is_not_null_in_the_migrated_db(migrated_db):
    """Stricter than the gate above for the one column tenancy hangs on. The gate tolerates a DB
    column looser than its model in general, but a nullable org_id is a row no shop can see
    (backend/db/tenancy.py): on Postgres the migrations left it nullable on 14 tenant tables."""
    loose = sorted(f"{op[2]}.{op[3]}" for op in _drift(migrated_db)
                   if op[0] == "modify_nullable" and op[3] == "org_id" and op[5] is True and op[6] is False)
    assert not loose, "org_id is NOT NULL in the model but nullable in the migrated DB:\n  " + "\n  ".join(loose)


def _migrated(migrated_db, fn):
    url = migrated_db if MIGRATED_ON_PG else f"sqlite+aiosqlite:///{migrated_db}"

    async def go():
        eng = create_async_engine(url, poolclass=NullPool)
        try:
            async with eng.connect() as conn:
                return await conn.run_sync(fn)
        finally:
            await eng.dispose()
    return db_isolation.run(go())


def test_every_model_check_constraint_and_partial_index_is_in_the_migrated_db(migrated_db):
    """Audit DB-16: alembic's compare never looks at CHECK constraints or partial-index predicates,
    so the gate above can't see one go missing. Every CHECK the models declare must exist (by name)
    on the migrated database, and every partial unique index must still be partial there."""
    from sqlalchemy import CheckConstraint, inspect

    def collect(sync_conn):
        insp = inspect(sync_conn)
        checks, partial = {}, {}
        for table in Base.metadata.sorted_tables:
            if any(isinstance(c, CheckConstraint) for c in table.constraints):
                checks[table.name] = {c["name"] for c in insp.get_check_constraints(table.name)}
        if MIGRATED_ON_PG:
            rows = sync_conn.exec_driver_sql("SELECT indexname, indexdef FROM pg_indexes").fetchall()
        else:
            rows = sync_conn.exec_driver_sql("SELECT name, sql FROM sqlite_master WHERE type = 'index' "
                                             "AND sql IS NOT NULL").fetchall()
        for name, sql in rows:
            partial[name] = " WHERE " in sql.upper()
        return checks, partial

    checks, partial = _migrated(migrated_db, collect)
    missing = []
    for table in Base.metadata.sorted_tables:
        for c in table.constraints:
            if isinstance(c, CheckConstraint) and c.name not in checks.get(table.name, set()):
                missing.append(f"CHECK {c.name} on {table.name}")
        for ix in table.indexes:
            where = ix.dialect_options["postgresql" if MIGRATED_ON_PG else "sqlite"].get("where")
            if ix.unique and where is not None and ix.name in partial and not partial[ix.name]:
                missing.append(f"partial predicate of {ix.name} on {table.name}")
    assert not missing, "The migrations don't build these (add them in a migration):\n  " + "\n  ".join(missing)


def test_fired_rule_delete_keeps_logs_on_migrated_schema(migrated_db):
    if MIGRATED_ON_PG:
        pytest.skip("SQLite-only probe (PRAGMA-driven inserts); the SQLite job runs it")
    c = sqlite3.connect(migrated_db)
    try:
        c.execute("PRAGMA foreign_keys=ON")  # the app enforces FKs
        # minimal parents for the NOT NULL org FK: insert with FKs off, then turn them back on
        c.execute("PRAGMA foreign_keys=OFF")
        cols = lambda t: {r[1]: r for r in c.execute(f"PRAGMA table_info({t})")}  # noqa: E731
        def ins(t, **kv):
            for name, r in cols(t).items():
                if r[3] and r[4] is None and name not in kv:   # NOT NULL without default
                    kv[name] = 0 if any(x in (r[2] or "").upper() for x in ("INT", "BOOL", "NUM", "FLOAT")) else "x"
            c.execute(f"INSERT INTO {t} ({','.join(kv)}) VALUES ({','.join('?' * len(kv))})", list(kv.values()))
        ins("notification_rules", id="rule-fired")
        for i in range(3):
            ins("notification_logs", id=f"log-{i}", rule_id="rule-fired")
        c.commit()
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("DELETE FROM notification_rules WHERE id='rule-fired'")  # used to be rejected
        c.commit()
        assert c.execute("SELECT COUNT(*) FROM notification_logs").fetchone()[0] == 3     # history kept
        assert c.execute("SELECT COUNT(*) FROM notification_logs WHERE rule_id IS NULL").fetchone()[0] == 3
    finally:
        c.close()


async def test_template_page_size_validated(client, tenant):
    base = {"name": "T", "doc_type": "invoice", "body_html": "<p>x</p>"}
    bad = await client.post("/api/documents/templates", json={**base, "page_size": "a4-landscape-x"}, headers=tenant)
    assert bad.status_code == 422, bad.text
    ok = await client.post("/api/documents/templates", json={**base, "page_size": "receipt58"}, headers=tenant)
    assert ok.status_code == 201, ok.text
    tid = ok.json()["id"]
    blank = await client.put(f"/api/documents/templates/{tid}", json={**base, "page_size": ""}, headers=tenant)
    assert blank.status_code == 200, blank.text          # empty keeps the historic fallback
    bad_put = await client.put(f"/api/documents/templates/{tid}", json={**base, "page_size": "tabloid"}, headers=tenant)
    assert bad_put.status_code == 422, bad_put.text
