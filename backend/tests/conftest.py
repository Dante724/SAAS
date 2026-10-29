"""Test fixtures.

Runs against an isolated SQLite database with the real app and a real schema,
overriding only the request DB dependency. ENVIRONMENT=test is set before the
app is imported so config/seed behave deterministically.
"""
import os

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SEED_DEMO", "false")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test.db")

# Under pytest-xdist each worker is its own process. Both the test engine (get_db override) AND
# the app engine (AsyncSessionLocal, used directly by some auth/platform tests) read their URL
# from the environment at import, so BOTH must get a per-worker SQLite file — otherwise workers
# deadlock on one shared file, or the app engine lands on an un-tabled DB. Must run BEFORE
# `from main import app` below (which builds the app engine from DATABASE_URL). No-op serially.
_xw = os.environ.get("PYTEST_XDIST_WORKER")
if _xw:
    for _var in ("DATABASE_URL", "TEST_DATABASE_URL"):
        _u = os.environ.get(_var)
        if _u and _u.startswith("sqlite") and _u.endswith(".db"):
            os.environ[_var] = _u[:-3] + f"_{_xw}.db"

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.db.database import Base, get_db
from backend.models import all_models  # noqa: F401 — registers tables
from main import app

# Overridable so the same suite can run against Postgres for dialect-compat checks:
#   TEST_DATABASE_URL=postgresql+asyncpg://... pytest ...
# Both URLs were already made worker-aware at the top (before app import). Default them to the
# app's DB so the test engine and app engine share one schema when TEST_DATABASE_URL is unset.
TEST_DB_URL = os.environ.get("TEST_DATABASE_URL") or os.environ["DATABASE_URL"]
if TEST_DB_URL.startswith("sqlite"):
    test_engine = create_async_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
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
    test_engine = create_async_engine(TEST_DB_URL, poolclass=NullPool)
TestSession = async_sessionmaker(test_engine, expire_on_commit=False)


async def _override_get_db():
    async with TestSession() as s:
        yield s


app.dependency_overrides[get_db] = _override_get_db


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


@pytest_asyncio.fixture(autouse=True)
async def _schema():
    # Dispose first so a connection leaked by a prior test (e.g. an endpoint that errored mid-
    # transaction) can't hold a write lock that makes this drop/create only partially apply —
    # which previously left a table present but stripped of its indexes for the next test.
    await test_engine.dispose()
    if test_engine.dialect.name == "postgresql":
        # On Postgres, drop_all + create_all of ~150 tables (twice per test, with the teardown drop)
        # is what made the serial suite take over an hour. Build the schema once per process, then
        # empty every table before each test — equivalent here: no test alters the schema, and no
        # table has a sequence, trigger or create-time seed data that a rebuild would have reset.
        # DELETE children-first in one round trip (~10ms) rather than TRUNCATE, which costs ~0.2s
        # for this many tables even when they are empty. The FK graph is acyclic apart from
        # self-references, which a single DELETE per table handles.
        global _pg_schema_built
        async with test_engine.begin() as conn:
            if not _pg_schema_built:
                await conn.run_sync(Base.metadata.drop_all)
                await conn.run_sync(Base.metadata.create_all)
                _pg_schema_built = True
            else:
                deletes = "".join(f'DELETE FROM "{t.name}";' for t in reversed(Base.metadata.sorted_tables))
                await conn.exec_driver_sql(f"DO $$BEGIN {deletes} END$$")
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
    """Each test starts with empty in-process rate-limit counters: every test client shares one
    IP, so sign-ups and logins from earlier tests would otherwise count against later ones."""
    from backend.services import ratelimit
    ratelimit._HITS.clear()
    yield


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
