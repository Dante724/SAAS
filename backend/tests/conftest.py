"""Test fixtures.

Runs against an isolated SQLite database with the real app and a real schema,
overriding only the request DB dependency. ENVIRONMENT=test is set before the
app is imported so config/seed behave deterministically.
"""
import os

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SEED_DEMO", "false")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test.db")

# Every test process gets its OWN database, so runs started at the same time (several sessions on
# one machine, or a quick run beside a full run) and the xdist workers inside one run can never see,
# drop or delete each other's tables. The configured URL is only the base:
#   SQLite   ./test.db            -> ./test_<run>[_gwN].db, deleted at the end of the run
#   Postgres .../repairos_test    -> database repairos_test_<run>[_gwN] on that server, created at
#                                    session start and dropped at the end (set TEST_KEEP_DB=1 to keep it)
# <run> is shared by the workers of one xdist run and unique per run. Both the test engine (get_db
# override) AND the app engine (AsyncSessionLocal, used directly by some auth/platform tests) read
# their URL from the environment at import, so BOTH are rewritten here, BEFORE `from main import app`.
import asyncio  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
import uuid  # noqa: E402

# Pure helpers: import nothing from the app (hang_guard: standard library only).
from backend.tests import db_isolation, hang_guard  # noqa: E402

assert "backend.config" not in sys.modules, "conftest must isolate DATABASE_URL before the app reads it"
_xw = os.environ.get("PYTEST_XDIST_WORKER")
_RUN_ID = (os.environ.get("PYTEST_XDIST_TESTRUNUID") or uuid.uuid4().hex)[:8] + (f"_{_xw}" if _xw else "")
_OWN_DBS: dict[str, str] = {}  # this process's database URL -> the configured URL it was made from
for _var in ("DATABASE_URL", "TEST_DATABASE_URL"):
    # xdist workers inherit the controller's environment, already rewritten: the configured URL is
    # kept aside so every process derives its name from the same base (not base_<ctrl>_<run>_gwN).
    _base = os.environ.get(f"TEKLY_BASE_{_var}") or os.environ.get(_var)
    if _base:
        os.environ[f"TEKLY_BASE_{_var}"] = _base
        os.environ[_var] = db_isolation.isolated_url(_base, _RUN_ID)
        _OWN_DBS[os.environ[_var]] = _base

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.db.database import Base, get_db
from backend.models import all_models  # noqa: F401 — registers tables
from backend.services.money import json_dumps  # same JSON-column serializer as the app engine
from main import app

# Overridable so the same suite can run against Postgres for dialect-compat checks:
#   TEST_DATABASE_URL=postgresql+asyncpg://... pytest ...
# Both URLs were already made worker-aware at the top (before app import). Default them to the
# app's DB so the test engine and app engine share one schema when TEST_DATABASE_URL is unset.
TEST_DB_URL = os.environ.get("TEST_DATABASE_URL") or os.environ["DATABASE_URL"]
if TEST_DB_URL.startswith("sqlite"):
    test_engine = create_async_engine(TEST_DB_URL, connect_args={"check_same_thread": False},
                                      json_serializer=json_dumps)
    # Enforce foreign keys so tests catch the integrity violations Postgres would raise in prod
    # (SQLite ignores FKs unless this pragma is set per-connection).
    from sqlalchemy import event as _event

    @_event.listens_for(test_engine.sync_engine, "connect")
    def _test_fk_on(dbapi_conn, _rec):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()
else:
    # asyncpg connections are bound to the event loop that created them, and pytest-asyncio
    # gives every test a fresh loop — so pooled connections would leak across loops and die
    # with "attached to a different loop". NullPool opens/closes per checkout instead.
    from sqlalchemy.pool import NullPool
    test_engine = create_async_engine(TEST_DB_URL, poolclass=NullPool, json_serializer=json_dumps)
TestSession = async_sessionmaker(test_engine, expire_on_commit=False)
# Postgres databases this process must create at the start and drop at the end, each with the
# configured database name it was made from (CREATE/DROP DATABASE connects there).
_PG_DBS = {u: make_url(base).database for u, base in _OWN_DBS.items() if make_url(u).get_backend_name() == "postgresql"}


async def _override_get_db():
    async with TestSession() as s:
        yield s


app.dependency_overrides[get_db] = _override_get_db


def _is_xdist_controller(config) -> bool:
    # When tests are distributed, the controller process collects but runs none: it needs no database.
    return config.pluginmanager.has_plugin("dsession")


def _is_xdist_worker(config) -> bool:
    return hasattr(config, "workerinput")


def _needs_db(config) -> bool:
    return not (_is_xdist_controller(config) or config.option.collectonly)


def pytest_configure(config):
    # Several tests use the `caplog` fixture, which the logging plugin provides. Without it they error
    # with "fixture 'caplog' not found" — noise that looks like real failures.
    if not config.pluginmanager.has_plugin("logging"):
        raise pytest.UsageError("Don't disable pytest's logging plugin (-p no:logging): tests use caplog.")


# --- the database server failing is an INFRASTRUCTURE error, not a test result --------------------
# Only this file's own code that OPENS a connection (session start, the start of `_schema` below, and
# a last check at session end) is classified, never a statement or a test body, so a real failure is
# never written off. On such an error the WHOLE run stops with exit code 75 and one clear message,
# instead of hundreds of misleading 500s and errors. A setup problem a rerun won't fix (no CREATEDB
# right, wrong password) stops it with exit code 4.
_pg_incarnation = None  # db_isolation.INCARNATION_SQL at session start; a change means a restart mid-run
_run_error: tuple[str, int] | None = None  # (message, exit code) once this process's database is unusable


def _stop_run(session, msg: str, code: int = db_isolation.INFRA_EXIT) -> None:
    global _run_error
    _run_error = (msg, code)
    session.shouldstop = msg  # under xdist this reaches the controller, which stops every worker
    if _is_xdist_worker(session.config):
        session.config.workeroutput["run_error"] = [msg, code]
    pytest.exit(msg, returncode=code)


def pytest_sessionstart(session):
    global _pg_incarnation, _run_error
    if session.config.option.collectonly:
        return
    controller = _is_xdist_controller(session.config)
    for url, base_db in _PG_DBS.items():
        try:
            if controller:  # check the server before starting any worker
                db_isolation.run(db_isolation.preflight_postgres(url, base_db))
                continue
            started = db_isolation.run(db_isolation.create_postgres(url, base_db))
        except Exception as e:  # noqa: BLE001 — any failure to reach the server is reported, never swallowed
            if db_isolation.is_infra_error(e):
                err = db_isolation.infra_message("the database server is unreachable or in recovery", url, e)
                code = db_isolation.INFRA_EXIT
            else:
                err = db_isolation.setup_message("could not create this run's test database", url, e)
                code = db_isolation.SETUP_EXIT
            if _is_xdist_worker(session.config):
                # pytest.exit this early makes xdist restart the worker over and over; the worker's
                # first test stops the run instead (see `_schema`), or its session end does.
                _run_error = (err, code)
                return
            pytest.exit(err, returncode=code)
        if url == TEST_DB_URL:
            _pg_incarnation = started


@pytest.hookimpl(optionalhook=True)
def pytest_testnodedown(node, error):
    # xdist controller: a worker stopped the run on a database error — carry it to the exit code.
    global _run_error
    reported = getattr(node, "workeroutput", {}).get("run_error")
    if reported and not _run_error:
        _run_error = tuple(reported)


def pytest_sessionfinish(session, exitstatus):
    global _run_error
    if _needs_db(session.config) and _pg_incarnation is not None and not _run_error:
        # A crash during this process's LAST test would otherwise go unnoticed.
        try:
            if db_isolation.run(db_isolation.incarnation(TEST_DB_URL)) != _pg_incarnation:
                _run_error = (db_isolation.infra_message(
                    "the database server crashed or restarted during the run", TEST_DB_URL), db_isolation.INFRA_EXIT)
        except Exception as e:  # noqa: BLE001
            if db_isolation.is_infra_error(e):
                _run_error = (db_isolation.infra_message(
                    "the database became unreachable during the run", TEST_DB_URL, e), db_isolation.INFRA_EXIT)
    if _run_error:
        if _is_xdist_worker(session.config):
            session.config.workeroutput["run_error"] = list(_run_error)
        else:
            sys.stderr.write("\n" + _run_error[0] + "\n")
        session.exitstatus = _run_error[1]
    if not _needs_db(session.config) or os.environ.get("TEST_KEEP_DB") == "1":
        return
    for url, base_db in _PG_DBS.items():
        for attempt in range(4):  # after a crash the server takes a few seconds to accept connections
            try:
                db_isolation.run(db_isolation.drop_postgres(url, base_db))
                break
            except Exception as e:  # noqa: BLE001 — a leftover database is harmless; say so and move on
                if attempt < 3 and db_isolation.is_infra_error(e):
                    time.sleep(2)
                    continue
                print(f"\nnote: could not drop test database {db_isolation.describe(url)}: {e}")
                break
    for url in _OWN_DBS:
        for f in db_isolation.sqlite_files(url):
            f.unlink(missing_ok=True)


# --- a hung test fails the run (with the culprit named) instead of hanging it ---------------------
# See backend/tests/hang_guard.py. TEST_HANG_TIMEOUT_S overrides the bound (0 turns it off).
def _db_sessions() -> list[str]:
    if make_url(TEST_DB_URL).get_backend_name() != "postgresql":
        return []
    return db_isolation.run(db_isolation.activity(TEST_DB_URL))


# An xdist worker's parent is the controller, which prints the report (the worker's stderr is lost).
_hang_owner = os.getppid() if _xw else os.getpid()
_watchdog = hang_guard.Watchdog(hang_guard.hang_timeout_s(), hang_guard.report_path(_hang_owner, _xw or "main"),
                                diagnostics=_db_sessions)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    _watchdog.start(item.nodeid)
    try:
        yield
    finally:
        _watchdog.cancel()


def pytest_terminal_summary(terminalreporter):
    for path in hang_guard.reports_for(os.getpid()):
        with open(path) as f:
            terminalreporter.write_sep("=", "HANG report", red=True)
            terminalreporter.write(f.read())
        os.unlink(path)


@pytest_asyncio.fixture(autouse=True)
async def _no_work_outlives_its_test():
    """Cancel whatever the test left running on its loop (see hang_guard): the loop is closed
    next without cancelling anything, which would freeze those tasks mid-transaction, holding
    their connections and row locks for the rest of the run."""
    _watchdog.loop = asyncio.get_running_loop()   # a hang report then shows where each task waits
    yield
    _, stuck = await hang_guard.cancel_leftover_tasks()
    if stuck:
        pytest.fail(f"tasks still running 10s after being cancelled at the end of the test: {stuck}")


@pytest_asyncio.fixture(autouse=True)
async def _reset_rate_limits():
    # The in-process rate limiters count every test's requests as one client IP within a 60s window,
    # so a fast run (e.g. Postgres without the per-test rebuild) trips the app-wide 600/min limit
    # partway through and unrelated tests get 429s. Start each test with empty counters.
    # RateLimitMiddleware counts through services.ratelimit here, so clearing _HITS covers it too.
    from backend.core.webhook_security import lookup_limiter
    from backend.services import ratelimit

    ratelimit._HITS.clear()
    lookup_limiter.reset()
    yield


_pg_schema_built = False
_CLEANUP_LOCK_TIMEOUT = "60s"


@pytest_asyncio.fixture(autouse=True)
async def _schema(request):
    # Dispose first so a connection leaked by a prior test (e.g. an endpoint that errored mid-
    # transaction) can't hold a write lock that makes this drop/create only partially apply —
    # which previously left a table present but stripped of its indexes for the next test.
    if _run_error:
        _stop_run(request.session, *_run_error)
    await test_engine.dispose()
    if test_engine.dialect.name == "postgresql":
        # On Postgres, drop_all + create_all of ~150 tables (twice per test, with the teardown drop)
        # is what made the serial suite take over an hour. Build the schema once per process, then
        # empty every table before each test — equivalent here: no test alters the schema, and no
        # table has a sequence or create-time seed data that a rebuild would have reset.
        # admin_action_log's append-only trigger refuses DELETE, so the cleanup runs with user
        # triggers off (SET LOCAL: this transaction only; tests still run with it on).
        # DELETE children-first in one round trip (~10ms) rather than TRUNCATE, which costs ~0.2s
        # for this many tables even when they are empty. The FK graph is acyclic apart from
        # self-references, which a single DELETE per table handles.
        # The database is this process's own (see the top of this file), so nothing else can drop or
        # empty its tables mid-run. A crash restart of the server is checked for on every test: the
        # tests before it may have failed for that reason alone, so the run stops as an infra error.
        global _pg_schema_built
        try:
            conn = await test_engine.connect()
            incarnation = tuple((await conn.exec_driver_sql(db_isolation.INCARNATION_SQL)).one())
            await conn.commit()
        except Exception as e:  # noqa: BLE001 — re-raised unless the server itself is unreachable
            if db_isolation.is_infra_error(e):
                _stop_run(request.session, db_isolation.infra_message(
                    "the database became unreachable during the run", TEST_DB_URL, e))
            raise
        try:
            if _pg_incarnation is not None and incarnation != _pg_incarnation:
                _stop_run(request.session, db_isolation.infra_message(
                    "the database server crashed or restarted during the run", TEST_DB_URL))
            # Bounded: a connection something left idle in a transaction must not make this wait
            # forever on its locks (that hung a CI shard silently). Fail with who holds them.
            try:
                async with conn.begin():
                    await conn.exec_driver_sql(f"SET LOCAL lock_timeout = '{_CLEANUP_LOCK_TIMEOUT}'")
                    if not _pg_schema_built:
                        await conn.run_sync(Base.metadata.drop_all)
                        await conn.run_sync(Base.metadata.create_all)
                        _pg_schema_built = True
                    else:
                        deletes = "".join(f'DELETE FROM "{t.name}";' for t in reversed(Base.metadata.sorted_tables))
                        await conn.exec_driver_sql("SET LOCAL session_replication_role = replica")
                        await conn.exec_driver_sql(f"DO $$BEGIN {deletes} END$$")
            except DBAPIError as e:
                if "lock timeout" not in str(e):
                    raise
                sessions = "\n  ".join(await db_isolation.activity(TEST_DB_URL)) or "(none)"
                raise RuntimeError(
                    f"emptying the tables before this test waited {_CLEANUP_LOCK_TIMEOUT} for a lock: an "
                    f"earlier test on this worker left a connection holding it. Sessions:\n  {sessions}") from e
        finally:
            await conn.close()
        yield
        return
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture(autouse=True)
def _fresh_rate_limits():
    """Each test starts with empty in-process rate-limit counters (every test client shares one
    IP) and an empty Tekly Sense per-shop service-history cache."""
    from backend.services import ratelimit, sense
    ratelimit._HITS.clear()
    sense._HISTORY_CACHE.clear()
    yield


@pytest.fixture
def card_readers_on(monkeypatch):
    """Card readers are off by default (settings.CARD_READERS_ENABLED); tests of the reader
    feature switch it on."""
    from backend.config import settings
    monkeypatch.setattr(settings, "CARD_READERS_ENABLED", True)


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def db():
    """Direct DB session against the same test database, for asserting on
    persisted state and exercising services with a controlled clock."""
    async with TestSession() as s:
        yield s


@pytest_asyncio.fixture
async def tenant(client):
    """Register a fresh org + admin, return (auth_headers, org_email)."""
    import uuid
    email = f"admin-{uuid.uuid4().hex[:8]}@test.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Test Co", "name": "Admin", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    token = r.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


# --- CI sharding --------------------------------------------------------------------------------
# CI splits the suite across parallel jobs with PYTEST_SHARD=<i> PYTEST_SHARD_TOTAL=<n> (1-based).
# Whole files are assigned greedily (largest first) to the shard with the fewest tests so far —
# per-test cost is dominated by the per-test schema rebuild above, so test count tracks runtime and
# the split stays balanced with no durations file to maintain. It is a pure function of the
# collected items, so every xdist worker (and every shard) computes the same partition, and the
# union of all shards is exactly the full suite. PYTEST_SHARD_MANIFEST, if set, receives this
# shard's node ids so CI can verify that union. Unset (the default): runs everything.
def pytest_collection_modifyitems(config, items):
    shard, total = os.environ.get("PYTEST_SHARD"), os.environ.get("PYTEST_SHARD_TOTAL")
    if not (shard and total):
        return
    shard, total = int(shard), int(total)
    assert 1 <= shard <= total, f"PYTEST_SHARD={shard} out of range 1..{total}"
    by_file: dict[str, list] = {}
    for item in items:
        by_file.setdefault(item.nodeid.split("::", 1)[0], []).append(item)
    loads = [0] * total
    owner: dict[str, int] = {}
    for path in sorted(by_file, key=lambda p: (-len(by_file[p]), p)):
        i = min(range(total), key=lambda k: (loads[k], k))
        owner[path] = i + 1
        loads[i] += len(by_file[path])
    keep = [it for it in items if owner[it.nodeid.split("::", 1)[0]] == shard]
    drop = [it for it in items if owner[it.nodeid.split("::", 1)[0]] != shard]
    if drop:
        config.hook.pytest_deselected(items=drop)
    items[:] = keep
    manifest = os.environ.get("PYTEST_SHARD_MANIFEST")
    if manifest and os.environ.get("PYTEST_XDIST_WORKER") in (None, "gw0"):
        with open(manifest, "w") as f:
            f.write("".join(it.nodeid + "\n" for it in keep))
