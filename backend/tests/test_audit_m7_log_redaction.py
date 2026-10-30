"""Audit 29 Oct 2026, M7: database error tracebacks printed the query parameters (emails, phones,
password hashes) into the logs and Sentry. Deployed engines hide parameters, and exception text
is redacted in both the log handler and the Sentry event."""
import logging

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from backend.config import Settings
from backend.core import error_tracking
from backend.core.logging import JsonFormatter, RedactionFilter
from backend.db.database import engine_kwargs

PII = "jane.doe@example.com"


def _db_error() -> Exception:
    from sqlalchemy.exc import IntegrityError
    return IntegrityError("INSERT INTO clients (email, phone) VALUES (?, ?)", (PII, "+15551234567"),
                          Exception("UNIQUE constraint failed: clients.email"))


def test_deployed_engine_hides_parameters():
    deployed = Settings.model_construct(ENVIRONMENT="production", DATABASE_URL="postgresql+asyncpg://u@db/t")
    local = Settings.model_construct(ENVIRONMENT="development", DATABASE_URL="sqlite+aiosqlite:///x.db")
    assert engine_kwargs(deployed)["hide_parameters"] is True
    assert engine_kwargs(local)["hide_parameters"] is False


@pytest.mark.asyncio
async def test_hidden_parameters_stay_out_of_the_error(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/h.db", hide_parameters=True)
    try:
        async with eng.begin() as c:
            await c.execute(text("CREATE TABLE t (email TEXT UNIQUE)"))
            await c.execute(text("INSERT INTO t VALUES (:e)"), {"e": PII})
            with pytest.raises(Exception) as e:
                await c.execute(text("INSERT INTO t VALUES (:e)"), {"e": PII})
        assert PII not in str(e.value)
    finally:
        await eng.dispose()


def _record_with(exc: Exception) -> logging.LogRecord:
    try:
        raise exc
    except Exception:
        import sys
        return logging.LogRecord("tekly", logging.ERROR, __file__, 1, "request failed", (), sys.exc_info())


@pytest.mark.parametrize("formatter", [logging.Formatter("%(message)s"), JsonFormatter()])
def test_log_tracebacks_are_redacted(formatter):
    rec = _record_with(_db_error())
    assert RedactionFilter().filter(rec)
    out = formatter.format(rec)
    assert PII not in out and "+15551234567" not in out
    assert "UNIQUE constraint failed" in out          # the useful part survives


def test_log_messages_mask_emails():
    rec = logging.LogRecord("tekly", logging.INFO, __file__, 1, "login failed for %s", (PII,), None)
    RedactionFilter().filter(rec)
    assert PII not in rec.getMessage()


def test_sentry_exception_values_are_redacted():
    ev = {"exception": {"values": [{"type": "IntegrityError", "value": str(_db_error())}]},
          "logentry": {"message": f"save failed for {PII}", "formatted": f"save failed for {PII}"},
          "breadcrumbs": {"values": [{"message": f"SELECT … [parameters: ('{PII}',)]"}]}}
    out = error_tracking._scrub(ev, None)
    assert PII not in repr(out)
    assert "UNIQUE constraint failed" in out["exception"]["values"][0]["value"]


def test_sentry_does_not_ship_local_variables(monkeypatch):
    import sentry_sdk

    from backend.config import settings
    seen = {}
    monkeypatch.setattr(settings, "SENTRY_DSN", "https://public@o0.ingest.example/1")
    monkeypatch.setattr(sentry_sdk, "init", lambda **kw: seen.update(kw))
    error_tracking.init_error_tracking()
    assert seen["include_local_variables"] is False


def test_sentry_request_url_tokens_are_masked():
    ev = {"request": {"url": "https://app.example.com/pay/Ab3dEf9hIjKlMnOpQrStUv12"},
          "message": f"failed for {PII}"}
    out = error_tracking._scrub(ev, None)
    assert "Ab3dEf9hIjKlMnOpQrStUv12" not in out["request"]["url"] and PII not in out["message"]
