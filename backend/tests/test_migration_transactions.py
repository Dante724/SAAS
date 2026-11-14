"""How a deploy's migrations hold locks, and that the whole chain can be walked back (final review
schema P2-1, P2-4, P3-1).

P2-1: env.py used to run every pending migration in one transaction, so the ACCESS EXCLUSIVE locks an
early migration took (dec08, w2b01, corg02, w4i01 ...) stayed held through the next migration's
read-only preflight scans: upgrading from pv01, dbi01's preflight ran with 49 tables locked, login
included. Now each migration commits on its own (transaction_per_migration), so a migration starts
holding no table locks at all.

P3-1: gc144g1 and idem01 both create idempotency_records only when it is missing, but both
downgrades dropped it unconditionally, so `alembic downgrade base` failed on the second one.

Postgres only (the backend-postgres CI job): SQLite has no pg_locks and serialises every writer.
"""
import os
import subprocess
import sys
import textwrap
import uuid

import pytest
from sqlalchemy.engine import make_url

from backend.tests import db_isolation
from backend.tests.test_dbi_migrations import _SUITE_URL, ON_PG, REPO, _alembic, _ok, _pg, _pg_insert

FIRST = "03851168a43b"   # the initial schema
pg_only = pytest.mark.skipif(not ON_PG, reason="pg_locks and Postgres transactional DDL: backend-postgres job")

# Runs alembic in-process and, the moment each migration's upgrade()/downgrade() is entered, prints
# the tables this session already holds ACCESS EXCLUSIVE locks on (left over from earlier migrations).
_PROBE = textwrap.dedent('''
    import functools, os, sys
    sys.path.insert(0, os.getcwd())
    from alembic.runtime.migration import RevisionStep

    LOCKS = ("SELECT c.relname FROM pg_locks l JOIN pg_class c ON c.oid = l.relation "
             "WHERE l.pid = pg_backend_pid() AND l.mode = 'AccessExclusiveLock' AND c.relkind IN ('r', 'p') "
             "ORDER BY 1")
    _init = RevisionStep.__init__

    def _probed_init(self, revision_map, revision, is_upgrade):
        _init(self, revision_map, revision, is_upgrade)
        fn = self.migration_fn

        @functools.wraps(fn)
        def probed(**kw):
            from alembic import op
            held = [r[0] for r in op.get_bind().exec_driver_sql(LOCKS).fetchall()]
            print(f"LOCKS-AT-START {revision.revision} {len(held)} {','.join(held)}", flush=True)
            return fn(**kw)
        self.migration_fn = probed

    RevisionStep.__init__ = _probed_init
    from alembic.config import main
    main(argv=["-c", "backend/alembic.ini", *sys.argv[1:]])
''')


def _fresh_db(tag: str) -> str:
    suite = make_url(_SUITE_URL)
    url = suite.set(database=f"{suite.database}_{tag}{uuid.uuid4().hex[:6]}").render_as_string(hide_password=False)
    db_isolation.run(db_isolation.create_postgres(url, suite.database))
    return url


def _probed(tmp_path, url: str, *args: str) -> dict[str, list[str]]:
    script = tmp_path / "lock_probe.py"
    script.write_text(_PROBE)
    env = dict(os.environ, ENVIRONMENT="test", SEED_DEMO="false", DATABASE_URL=url)
    env.pop("TEST_DATABASE_URL", None)
    r = subprocess.run([sys.executable, str(script), *args], cwd=REPO, env=env,
                       capture_output=True, text=True, timeout=900)
    assert r.returncode == 0, f"alembic {args} failed:\n{r.stderr[-4000:]}"
    held = {}
    for line in r.stdout.splitlines():
        if line.startswith("LOCKS-AT-START "):
            _tag, rev, n, names = (line.split(" ", 3) + [""])[:4]
            held[rev] = [t for t in names.split(",") if t] if int(n) else []
    return held


@pg_only
def test_each_migration_starts_holding_no_table_locks_from_the_ones_before(tmp_path):
    """Upgrading from empty to head: no migration (dbi01's and w4s01's preflights included) begins
    while an earlier migration's ACCESS EXCLUSIVE locks are still held."""
    url = _fresh_db("lk")
    try:
        held = _probed(tmp_path, url, "upgrade", "head")
        assert len(held) > 100, "the probe saw every migration"
        for rev in ("dbi01", "w4s01", "corg02", "dbi03"):
            assert rev in held, f"{rev} ran"
        # On an empty database alembic creates alembic_version in the first migration's transaction.
        held[FIRST] = [t for t in held[FIRST] if t != "alembic_version"]
        locked = {rev: tables for rev, tables in held.items() if tables}
        assert not locked, f"migrations started with earlier migrations' table locks still held: {locked}"
    finally:
        db_isolation.run(db_isolation.drop_postgres(url, make_url(_SUITE_URL).database))


@pg_only
def test_a_failed_migration_keeps_the_ones_before_it(tmp_path):
    """Each migration commits on its own: a later one failing does not roll back (or hold the locks
    of) the ones that already finished, so a retry resumes where it stopped."""
    url = _fresh_db("pm")
    try:
        _ok(url, "upgrade", "dbi03")
        # w4s01 stops (counts only) on a negative payment, which it will not guess how to repair.
        _pg(url, _pg_insert(url, "organisations", {"id": "orgA", "name": "A", "account_id": "acct-A"}),
            _pg_insert(url, "locations", {"id": "loc-A", "org_id": "orgA", "name": "Main"}))
        _pg(url, _pg_insert(url, "orders", {"id": "ordA", "org_id": "orgA", "location_id": "loc-A",
                                            "public_token": "tok-A"}),
            _pg_insert(url, "payments", {"id": "pay-neg", "org_id": "orgA", "order_id": "ordA", "method": "cash",
                                         "kind": "payment", "amount": -3}))
        r = _alembic(url, "upgrade", "head")          # dbi03 -> w4i01 (fine) -> w4s01 (stops)
        assert r.returncode != 0 and "negative amount" in r.stderr
        assert _pg(url, "SELECT version_num FROM alembic_version") == [("w4i01",)], "w4i01 stayed committed"
        assert _pg(url, "SELECT 1 FROM information_schema.columns WHERE table_name = 'order_lines' "
                        "AND column_name = 'line_role'") == [], "w4s01 left nothing behind"
    finally:
        db_isolation.run(db_isolation.drop_postgres(url, make_url(_SUITE_URL).database))


@pg_only
def test_downgrade_base_then_upgrade_head(tmp_path):
    """The whole chain walks back to an empty database and up again (the two branches that share
    idempotency_records no longer both drop it)."""
    url = _fresh_db("db")
    try:
        _ok(url, "upgrade", "head")
        _ok(url, "downgrade", "base")
        assert _pg(url, "SELECT version_num FROM alembic_version") == []
        assert not _pg(url, "SELECT 1 FROM information_schema.tables WHERE table_name = 'idempotency_records'")
        _ok(url, "upgrade", "head")
        _ok(url, "downgrade", "-1")
        _ok(url, "upgrade", "head")
        assert not _pg(url, "SELECT indexrelid::regclass FROM pg_index WHERE NOT indisvalid")
    finally:
        db_isolation.run(db_isolation.drop_postgres(url, make_url(_SUITE_URL).database))
