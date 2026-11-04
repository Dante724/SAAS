"""One private database per test process, and a loud stop when the database itself goes away.

conftest.py imports this BEFORE the app (the app engine reads DATABASE_URL at import), so this module
must not import anything from the app — only the standard library, SQLAlchemy's URL parser and asyncpg.

- `isolated_url` turns the configured base URL into this process's own database: a uniquely named
  SQLite file next to the configured one, or a uniquely named database on the same Postgres server.
- `create_postgres` / `drop_postgres` make and remove that database (a preflight first: an
  unreachable server, or one in recovery, stops the run before any test starts).
- `is_infra_error` says whether an exception means "the database server is down / restarting / out
  of connections" rather than "the code under test is wrong". conftest only applies it to its own
  code that opens a connection, never to a statement or a test body, so a real failure is never
  reclassified as infrastructure.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

from sqlalchemy.engine import make_url

# Changes whenever the server restarts. pg_postmaster_start_time() alone does NOT change when one backend
# crashes (e.g. killed for memory) and the postmaster restarts every connection and runs crash recovery;
# that resets the shared statistics, so their reset time does change.
INCARNATION_SQL = "SELECT pg_postmaster_start_time(), (SELECT stats_reset FROM pg_stat_bgwriter)"

# EX_TEMPFAIL: "temporary failure, try again". A run that stops with it proved nothing either way.
INFRA_EXIT = 75
# A setup problem that a rerun won't fix (no CREATEDB right, wrong password): pytest's usage error.
SETUP_EXIT = 4


def isolated_url(url: str, run_id: str) -> str:
    """`url` rewritten to a database that belongs to `run_id` alone. In-memory SQLite is already
    private to the process and is returned unchanged."""
    u = make_url(url)
    if u.get_backend_name() == "sqlite":
        db = u.database or ""
        if not db or db == ":memory:" or db.startswith("file:"):
            return url
        p = Path(db)
        stem, suffix = (p.stem, p.suffix) if p.suffix else (p.name, "")
        return u.set(database=str(p.with_name(f"{stem}_{run_id}{suffix}"))).render_as_string(hide_password=False)
    if u.get_backend_name() == "postgresql":
        return u.set(database=pg_db_name(u.database or "postgres", run_id)).render_as_string(hide_password=False)
    return url


def pg_db_name(base: str, run_id: str) -> str:
    """`<base>_<run_id>`, lower-case, safe as an unquoted identifier, within Postgres's 63 bytes."""
    tail = "_" + re.sub(r"[^a-z0-9_]", "_", run_id.lower())
    head = re.sub(r"[^a-z0-9_]", "_", base.lower())[: 63 - len(tail)] or "t"
    return head + tail


def sqlite_files(url: str) -> list[Path]:
    """The SQLite database file for `url` and its journal/WAL siblings (empty for non-file URLs)."""
    u = make_url(url)
    if u.get_backend_name() != "sqlite" or not u.database or u.database == ":memory:" \
            or u.database.startswith("file:"):
        return []
    p = Path(u.database)
    return [p] + [p.with_name(p.name + s) for s in ("-journal", "-wal", "-shm")]


def describe(url: str) -> str:
    """The URL with any password masked, for messages."""
    return make_url(url).render_as_string(hide_password=True)


def _asyncpg_dsn(url: str, database: str) -> str:
    return make_url(url).set(drivername="postgresql", database=database).render_as_string(hide_password=False)


async def _admin_connect(url: str, base_db: str):
    """A connection to the server for CREATE/DROP DATABASE. Uses the configured (base) database,
    which the caller already relies on existing, else the standard `postgres` one."""
    import asyncpg

    kw = dict(timeout=10, command_timeout=60)   # a hung CREATE/DROP DATABASE can't hang the run
    try:
        return await asyncpg.connect(_asyncpg_dsn(url, base_db), **kw)
    except asyncpg.InvalidCatalogNameError:
        return await asyncpg.connect(_asyncpg_dsn(url, "postgres"), **kw)


async def _check_primary(conn) -> None:
    if await conn.fetchval("SELECT pg_is_in_recovery()"):
        raise RecoveryModeError("the server is in recovery (a crash restart or a read-only standby)")


REPLICA_ROLE_HINT = (
    "Cleaning up between tests runs `SET LOCAL session_replication_role = replica` (so the per-test DELETEs\n"
    "  skip foreign-key triggers). The test role must be SUPERUSER, or on Postgres 15+ be granted:\n"
    "    GRANT SET ON PARAMETER session_replication_role TO <role>;\n"
    "  scripts/test_pg.sh's throwaway server already uses a superuser. A rerun won't help."
)


class MissingPrivilegeError(Exception):
    """The role can reach the server but lacks a privilege the suite needs (not an outage: never infra)."""


async def _check_privileges(conn) -> None:
    """One probe per run: the between-tests cleanup in conftest's `_schema` needs this setting."""
    from asyncpg import exceptions as ape

    tr = conn.transaction()
    await tr.start()
    try:
        await conn.execute("SET LOCAL session_replication_role = replica")
    except ape.InsufficientPrivilegeError as e:
        raise MissingPrivilegeError(f"the test role can't set session_replication_role ({e})") from e
    finally:
        await tr.rollback()


async def preflight_postgres(url: str, base_db: str) -> None:
    """Raise unless the server is up and writable and the role has what the suite needs. Creates nothing."""
    conn = await _admin_connect(url, base_db)
    try:
        await _check_primary(conn)
        await _check_privileges(conn)
    finally:
        await conn.close()


async def create_postgres(url: str, base_db: str):
    """Preflight the server, then create this process's database. Returns the server's INCARNATION_SQL row, so a restart mid-run can be noticed."""
    conn = await _admin_connect(url, base_db)
    try:
        await _check_primary(conn)
        await _check_privileges(conn)  # before CREATE, so a run that can't proceed leaves nothing behind
        name = make_url(url).database
        # No DROP first: names are unique per run, so an existing one belongs to someone else — fail.
        # template0: CREATE DATABASE from template1 fails if anyone else is connected to template1.
        await conn.execute(f'CREATE DATABASE "{name}" TEMPLATE template0')
        return tuple(await conn.fetchrow(INCARNATION_SQL))
    finally:
        await conn.close()


async def incarnation(url: str):
    """INCARNATION_SQL, read from this process's own database."""
    import asyncpg

    conn = await asyncpg.connect(_asyncpg_dsn(url, make_url(url).database), timeout=10, command_timeout=60)
    try:
        return tuple(await conn.fetchrow(INCARNATION_SQL))
    finally:
        await conn.close()


async def drop_postgres(url: str, base_db: str) -> None:
    conn = await _admin_connect(url, base_db)
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{make_url(url).database}" WITH (FORCE)')
    finally:
        await conn.close()


class RecoveryModeError(Exception):
    """The server accepted the connection but is not a writable primary."""


def is_infra_error(exc: BaseException) -> bool:
    """True when `exc` (or anything it wraps) says the database server could not be reached: down,
    restarting, in recovery, out of connection slots, or this run's own database gone. conftest only
    asks this about OPENING a connection, never about a statement or a test body. A statement timeout
    anywhere in the chain means the code's problem, whatever else the chain holds."""
    from asyncpg import exceptions as ape

    infra = (
        ConnectionError,                # refused, reset, aborted
        FileNotFoundError,              # the server's unix socket is gone
        TimeoutError,                   # connect timed out (asyncio.TimeoutError is this, 3.11+)
        RecoveryModeError,
        ape.PostgresConnectionError,    # class 08: connection failure / does not exist / rejected
        ape.CannotConnectNowError,      # 57P03: starting up, shutting down, in recovery mode
        ape.AdminShutdownError,         # 57P01: terminated by administrator
        ape.CrashShutdownError,         # 57P02: another server process crashed
        ape.TooManyConnectionsError,    # 53300: too many parallel runs (or a leak: the message says so)
        ape.InvalidCatalogNameError,    # this run's OWN database is gone: someone dropped it, or deleted
                                        # the server's data dir (what happened on 2026-11-04)
    )
    chain: list[BaseException] = []
    stack: list[BaseException | None] = [exc]
    while stack:
        e = stack.pop()
        if e is None or any(e is c for c in chain):
            continue
        chain.append(e)
        stack += [getattr(e, "orig", None), e.__cause__, e.__context__]
    if any(isinstance(e, ape.QueryCanceledError) for e in chain):
        return False
    return any(isinstance(e, infra) for e in chain)


def infra_message(what: str, url: str, exc: BaseException | None = None) -> str:
    err = f"\n  cause: {type(exc).__name__}: {(str(exc).strip().splitlines() or [''])[0][:300]}" if exc else ""
    from asyncpg import exceptions as ape

    if isinstance(exc, ape.TooManyConnectionsError) or "too many clients" in err:
        err += ("\n  If few runs share this server, this run may be LEAKING connections (a real bug): "
                "count pg_stat_activity rows for its database.")
    return (
        f"INFRASTRUCTURE ERROR — {what}\n"
        f"  database: {describe(url)}{err}\n"
        "  This run stopped on purpose. Its results prove nothing, either way: tests that failed just\n"
        "  before this point may have failed because of the outage. Fix the database server (is it\n"
        "  running? out of memory? too many parallel runs?) and rerun. See TESTING.md, 'Running tests\n"
        "  in parallel safely'."
    )


def setup_message(what: str, url: str, exc: BaseException) -> str:
    if isinstance(exc, MissingPrivilegeError):
        what, hint = "the test database role lacks a privilege the suite needs", REPLICA_ROLE_HINT
    else:
        hint = ("The server answered, but this run can't make its own database there. A rerun won't help: check\n"
                "  the role's CREATEDB right and password, or use scripts/test_pg.sh (a throwaway server).")
    return (
        f"TEST SETUP ERROR — {what}\n"
        f"  database: {describe(url)}\n"
        f"  cause: {type(exc).__name__}: {(str(exc).strip().splitlines() or [''])[0][:300]}\n"
        f"  {hint}"
    )


def run(coro):
    """Run a coroutine from a pytest hook (no event loop is running there)."""
    return asyncio.run(coro)
