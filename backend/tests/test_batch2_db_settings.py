"""Batch 2 — Postgres sessions get a statement timeout and an idle-in-transaction timeout."""
from types import SimpleNamespace

from backend.db.database import pg_connect_args


def _cfg(**kw):
    base = dict(DATABASE_URL="postgresql+asyncpg://u:p@h/db", DB_STATEMENT_TIMEOUT_MS=15000,
                DB_IDLE_IN_TX_TIMEOUT_MS=60000)
    return SimpleNamespace(**{**base, **kw})


def test_asyncpg_gets_timeouts():
    ss = pg_connect_args(_cfg())["server_settings"]
    assert ss == {"statement_timeout": "15000", "idle_in_transaction_session_timeout": "60000"}


def test_timeouts_can_be_turned_off_and_sqlite_gets_none():
    assert pg_connect_args(_cfg(DB_STATEMENT_TIMEOUT_MS=0, DB_IDLE_IN_TX_TIMEOUT_MS=0)) == {}
    assert pg_connect_args(_cfg(DB_IDLE_IN_TX_TIMEOUT_MS=0))["server_settings"] == {"statement_timeout": "15000"}
    assert pg_connect_args(_cfg(DATABASE_URL="sqlite+aiosqlite:///x.db")) == {}
