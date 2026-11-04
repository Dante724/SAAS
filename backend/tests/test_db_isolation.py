"""Test-infrastructure guarantees (backend/tests/db_isolation.py + conftest):

- every test process works on its own database, so concurrent runs and xdist workers can't collide;
- only "the database server is down / restarting / out of connections" counts as an infrastructure
  error — anything the code under test can cause (timeouts, constraint errors, misuse) does not.
"""
import asyncio
import os
import re

import pytest
from asyncpg import exceptions as ape
from sqlalchemy.exc import DBAPIError

from backend.tests import db_isolation as iso


def test_sqlite_file_gets_a_per_run_name_next_to_the_configured_one():
    assert iso.isolated_url("sqlite+aiosqlite:///./test.db", "ab12cd34_gw3") == "sqlite+aiosqlite:///test_ab12cd34_gw3.db"
    assert iso.isolated_url("sqlite+aiosqlite:////abs/dir/ci_test.db", "r1") == "sqlite+aiosqlite:////abs/dir/ci_test_r1.db"
    assert iso.isolated_url("sqlite+aiosqlite:///./noext", "r1") == "sqlite+aiosqlite:///noext_r1"


def test_in_memory_sqlite_is_already_private():
    for url in ("sqlite+aiosqlite://", "sqlite+aiosqlite:///:memory:"):
        assert iso.isolated_url(url, "r1") == url


def test_two_runs_never_share_a_database():
    for base in ("sqlite+aiosqlite:///./test.db", "postgresql+asyncpg://u:p@localhost:5432/repairos_test"):
        urls = {iso.isolated_url(base, r) for r in ("aaaa1111", "bbbb2222", "aaaa1111_gw0", "aaaa1111_gw1")}
        assert len(urls) == 4 and base not in urls


def test_postgres_keeps_server_credentials_and_options():
    url = iso.isolated_url("postgresql+asyncpg://u:s3cret@/repairos_test?host=/tmp/sock&port=5555", "ab12cd34_gw0")
    assert url == "postgresql+asyncpg://u:s3cret@/repairos_test_ab12cd34_gw0?host=%2Ftmp%2Fsock&port=5555"


def test_postgres_name_is_a_safe_identifier_within_63_bytes():
    name = iso.pg_db_name("Some-Very_" + "long" * 30, "AB12cd34_gw12")
    assert len(name) <= 63 and re.fullmatch(r"[a-z0-9_]+", name) and name.endswith("_ab12cd34_gw12")


def test_sqlite_cleanup_covers_journal_and_wal_files():
    names = [p.name for p in iso.sqlite_files("sqlite+aiosqlite:///./x_r1.db")]
    assert names == ["x_r1.db", "x_r1.db-journal", "x_r1.db-wal", "x_r1.db-shm"]
    assert iso.sqlite_files("sqlite+aiosqlite://") == []
    assert iso.sqlite_files("postgresql+asyncpg://u@h/db") == []


def test_this_run_uses_its_own_database():
    # The app engine and the test engine were both pointed at this process's isolated database.
    from backend.config import settings

    assert settings.DATABASE_URL == os.environ["DATABASE_URL"]
    worker = os.environ.get("PYTEST_XDIST_WORKER")
    tail = r"_[0-9a-f]{8}" + (f"_{worker}" if worker else "")
    assert re.search(tail + r"(\.db)?(\?.*)?$", settings.DATABASE_URL), settings.DATABASE_URL


def _wrapped(orig: BaseException) -> DBAPIError:
    """How SQLAlchemy hands an asyncpg error to the code: DBAPIError -> adapter error -> asyncpg error."""
    adapter = Exception(str(orig))
    adapter.__cause__ = orig
    return DBAPIError("SELECT 1", {}, adapter)


@pytest.mark.parametrize("exc", [
    ConnectionRefusedError(61, "Connection refused"),
    FileNotFoundError(2, "No such file or directory"),        # socket gone
    asyncio.TimeoutError(),
    ape.CannotConnectNowError("the database system is in recovery mode"),
    ape.CrashShutdownError("terminating connection because of crash of another server process"),
    ape.AdminShutdownError("terminating connection due to administrator command"),
    ape.TooManyConnectionsError("sorry, too many clients already"),
    ape.ConnectionDoesNotExistError("connection was closed in the middle of operation"),
    iso.RecoveryModeError("in recovery"),
    ape.InvalidCatalogNameError('database "repairos_test_ab12cd34_gw0" does not exist'),
])
def test_server_outages_are_infrastructure(exc):
    assert iso.is_infra_error(exc)
    assert iso.is_infra_error(_wrapped(exc))


@pytest.mark.parametrize("exc", [
    ape.QueryCanceledError("canceling statement due to statement timeout"),
    ape.LockNotAvailableError("could not obtain lock"),
    ape.DeadlockDetectedError("deadlock detected"),
    ape.UniqueViolationError("duplicate key value"),
    ape.UndefinedTableError('relation "stocktake_serials" does not exist'),
    ape.InterfaceError("cannot perform operation: another operation is in progress"),
    ape.OutOfMemoryError("out of shared memory"),             # max_locks_per_transaction: the code's size
    PermissionError(13, "Permission denied"),
    AssertionError("assert 500 == 200"),
    ValueError("bad input"),
])
def test_code_errors_are_never_infrastructure(exc):
    assert not iso.is_infra_error(exc)
    assert not iso.is_infra_error(_wrapped(exc))


def test_a_statement_timeout_anywhere_in_the_chain_is_the_codes_problem():
    # Whatever order the chain is walked in: a timeout plus a reset connection is still a timeout.
    timeout = ape.QueryCanceledError("canceling statement due to statement timeout")
    outer = _wrapped(timeout)
    outer.__context__ = ConnectionResetError(54, "Connection reset by peer")
    assert not iso.is_infra_error(outer)


def test_infra_message_hides_the_password_and_says_to_rerun():
    msg = iso.infra_message("the database server is unreachable", "postgresql+asyncpg://u:s3cret@h/db_r1",
                            ConnectionRefusedError(61, "Connection refused"))
    assert msg.startswith("INFRASTRUCTURE ERROR") and "s3cret" not in msg and "rerun" in msg


class _FakeConn:
    """Just enough of an asyncpg connection for the privilege probe."""

    def __init__(self, error=None):
        self.error, self.rolled_back = error, False

    def transaction(self):
        conn = self

        class _Tr:
            async def start(self):
                pass

            async def rollback(self):
                conn.rolled_back = True

        return _Tr()

    async def execute(self, sql):
        assert sql == "SET LOCAL session_replication_role = replica"
        if self.error:
            raise self.error


def test_privilege_probe_passes_for_a_role_that_can_set_replica_mode():
    conn = _FakeConn()
    asyncio.run(iso._check_privileges(conn))
    assert conn.rolled_back  # the probe changes nothing


def test_a_role_without_superuser_stops_the_run_as_a_setup_error_not_infra():
    conn = _FakeConn(ape.InsufficientPrivilegeError('permission denied to set parameter "session_replication_role"'))
    with pytest.raises(iso.MissingPrivilegeError) as info:
        asyncio.run(iso._check_privileges(conn))
    assert conn.rolled_back
    assert not iso.is_infra_error(info.value)  # a rerun can't fix it, so never exit 75
    msg = iso.setup_message("could not create this run's test database", "postgresql+asyncpg://u:s3cret@h/db",
                            info.value)
    assert msg.startswith("TEST SETUP ERROR — the test database role lacks a privilege")
    assert "SUPERUSER" in msg and "GRANT SET ON PARAMETER session_replication_role" in msg and "s3cret" not in msg
