"""Final-review fix wave X4: bad input is a 4xx, never a 500.

* QA-1: on Postgres, asyncpg's overflow / too-long errors reach SQLAlchemy as a plain
  ``DBAPIError`` (not ``DataError``), so the N3 422 mapping never ran. The handler now branches on
  the driver's SQLSTATE for any DBAPIError. Tested through the real driver, not a hand-built error.
* schema P2-2: a quantity below 0.00005 passed ``gt=0`` but NUMERIC(14,4) stored 0 and the
  ``qty <> 0`` CHECK fired (500). ``Qty`` rounds to the column's scale and refuses a quantity that
  rounds to nothing; a CHECK violation (23514) that still gets through is a 422.
* QA-2: a NUL byte in a query value, path or JSON string is refused with a 422 before routing.
* QA-3: huge or negative integers (skip, priority, ...) are a 422.
* QA-4: a malformed date on /finance/pnl and /reports/job-profitability is a 422.
* A fuzz walk over every GET route asserts none of those inputs gives a 500.
"""
import json
import os

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel, ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.services.money import Qty

_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL", "")
IS_PG = _URL.startswith("postgresql")
pg_only = pytest.mark.skipif(not IS_PG, reason="the driver error path is Postgres (asyncpg) only")

HUGE = 10 ** 20


@pytest.fixture
async def client():
    """Like conftest's client, but a 500 comes back as a response instead of raising."""
    from main import app
    async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False),
                           base_url="http://test") as c:
        yield c


# ── QA-1: real driver errors through the handler ─────────────────────────────
def _mini_app(engine):
    """A tiny app with the production DB-error handlers, whose routes run raw SQL on the real test
    database: the errors are whatever the driver actually raises."""
    from backend.core.db_errors import install_db_error_handlers

    mini = FastAPI()
    install_db_error_handlers(mini)

    cases = {
        "numeric": [("SELECT CAST(:v AS NUMERIC(5,2))", {"v": 1e9})],
        "int_server": [("SELECT CAST(2147483647 AS INTEGER) + 1", {})],
        "too_long": [("CREATE TEMP TABLE x4_tl (s VARCHAR(3))", {}),
                     ("INSERT INTO x4_tl VALUES (:s)", {"s": "abcdef"})],
        "nul": [("SELECT CAST(:v AS TEXT)", {"v": "a\x00b"})],
        "int_param": [("SELECT 1 OFFSET :v", {"v": HUGE})],
        "neg_offset": [("SELECT 1 OFFSET :v", {"v": -1})],
        "check": [("CREATE TEMP TABLE x4_ck (q NUMERIC(14,4) CHECK (q <> 0))", {}),
                  ("INSERT INTO x4_ck VALUES (:q)", {"q": 0.00001})],
        "bad_cast": [("SELECT CAST('abc' AS UUID)", {})],
    }

    @mini.get("/case/{name}")
    async def case(name: str):
        async with AsyncSession(engine) as s:
            for sql, params in cases[name]:
                await s.execute(text(sql), params)
        return {}

    return mini


@pg_only
@pytest.mark.parametrize("name, needle", [
    ("numeric", "too large"),
    ("int_server", "too large"),
    ("int_param", "too large"),
    ("too_long", "too long"),
    ("nul", "NUL"),
    ("neg_offset", "negative"),
    ("check", "allowed range"),
])
async def test_real_postgres_driver_errors_are_422s(db, name, needle):
    mini = _mini_app(db.bind)
    async with AsyncClient(transport=ASGITransport(app=mini, raise_app_exceptions=False), base_url="http://t") as c:
        r = await c.get(f"/case/{name}")
    assert r.status_code == 422, (name, r.status_code, r.text)
    detail = r.json()["detail"]
    assert needle.lower() in detail.lower(), detail
    assert "SELECT" not in detail and "x4_" not in detail and "$1" not in detail   # no SQL leaks out


@pg_only
async def test_other_driver_errors_stay_500s(db):
    mini = _mini_app(db.bind)
    async with AsyncClient(transport=ASGITransport(app=mini, raise_app_exceptions=False), base_url="http://t") as c:
        r = await c.get("/case/bad_cast")
    assert r.status_code == 500


async def test_main_app_registers_the_dbapi_handler():
    from sqlalchemy.exc import DataError, DBAPIError, IntegrityError

    from main import app
    for cls in (DBAPIError, DataError, IntegrityError):
        assert cls in app.exception_handlers


async def test_too_long_client_name_is_not_a_500(client, tenant):
    r = await client.post("/api/clients", json={"name": "x" * 100_000}, headers=tenant)
    assert r.status_code != 500, r.text
    if IS_PG:
        assert r.status_code == 422


# ── schema P2-2: quantities finer than the column ────────────────────────────
class _Line(BaseModel):
    qty: Qty


def test_qty_rounds_to_the_column_scale():
    assert _Line(qty=1.23456).qty == 1.2346
    assert _Line(qty=-0.00005).qty == -0.0001
    assert _Line(qty=0).qty == 0          # zero is each field's own call (gt=0 etc.)


@pytest.mark.parametrize("q", [0.00001, 0.00004999, -0.00004])
def test_qty_too_small_to_store_is_refused(q):
    with pytest.raises(ValidationError) as e:
        _Line(qty=q)
    assert "too small" in str(e.value)


async def test_tiny_credit_note_qty_is_a_422(client, tenant):
    r = await client.post("/api/finance/credit-notes", headers=tenant, json={
        "reason": "x", "lines": [{"description": "a", "qty": 0.00001, "unit_price": 1}]})
    assert r.status_code == 422, r.text


async def test_sqlite_or_pg_check_violation_maps_to_422():
    """The 23514 backstop: SQLite reports a CHECK as text, Postgres by SQLSTATE."""
    from sqlalchemy.exc import IntegrityError

    from backend.core.db_errors import integrity_error_handler
    exc = IntegrityError("INSERT INTO sale_lines ...", {}, Exception("CHECK constraint failed: qty_nonzero"))
    r = await integrity_error_handler(None, exc)
    assert r.status_code == 422 and "sale_lines" not in json.loads(r.body)["detail"]


# ── QA-2: NUL bytes ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("url", [
    "/api/orders?search=%00",
    "/api/orders?search=a%00b",
    "/api/clients?search=%00",
    "/api/clients/%00",
    "/api/public/branding?domain=%00",
])
async def test_nul_in_query_or_path_is_a_422(client, tenant, url):
    r = await client.get(url, headers=tenant)
    assert r.status_code == 422, (url, r.status_code, r.text)
    assert "NUL" in r.json()["detail"]


@pytest.mark.parametrize("path, body", [
    ("/api/clients", {"name": "A\u0000B"}),
    ("/api/tasks", {"title": "t\u0000"}),
    ("/api/clients", {"name": "ok", "tags": ["x", {"deep": "a\u0000"}]}),
])
async def test_nul_in_json_body_is_a_422(client, tenant, path, body):
    r = await client.post(path, json=body, headers=tenant)
    assert r.status_code == 422, r.text
    assert "NUL" in r.json()["detail"]


async def test_escaped_backslash_u0000_text_is_allowed(client, tenant):
    # The JSON text \\u0000 is the six characters "\u0000", not a NUL byte.
    raw = '{"name": "slash \\\\u0000 kept"}'
    r = await client.post("/api/clients", content=raw, headers={**tenant, "content-type": "application/json"})
    assert r.status_code in (200, 201), r.text
    assert r.json()["name"] == "slash \\u0000 kept"


# ── QA-3: unbounded integers ─────────────────────────────────────────────────
@pytest.mark.parametrize("url", [
    f"/api/orders?skip={HUGE}",
    f"/api/clients?skip={HUGE}",
    f"/api/orders?priority={HUGE}",
    f"/api/orders?priority_min={HUGE}",
    "/api/inventory/parts?skip=-1",
    f"/api/inventory/parts?skip={HUGE}",
])
async def test_huge_or_negative_ints_are_not_500(client, tenant, url):
    r = await client.get(url, headers=tenant)
    assert r.status_code != 500, (url, r.text)
    if IS_PG or HUGE_IN(url):
        assert r.status_code == 422, (url, r.status_code, r.text)


def HUGE_IN(url: str) -> bool:     # noqa: N802 — SQLite takes skip=-1 as 0; huge values never fit
    return str(HUGE) in url


async def test_huge_order_priority_body_is_not_a_500(client, tenant):
    r = await client.post("/api/orders", json={"priority": 99_999_999_999}, headers=tenant)
    assert r.status_code != 500, r.text


# ── QA-4: malformed dates ────────────────────────────────────────────────────
@pytest.mark.parametrize("url", [
    "/api/finance/pnl?date_from=garbage",
    "/api/finance/pnl?date_to=2026-13-45",
    "/api/reports/job-profitability?from_=garbage",
    "/api/reports/job-profitability?to=garbage",
])
async def test_malformed_report_dates_are_422(client, tenant, url):
    r = await client.get(url, headers=tenant)
    assert r.status_code == 422, (url, r.status_code, r.text)


def test_range_edge_still_raises_value_error_for_callers_that_catch_it():
    from backend.services.dates import range_edge, report_edge
    for fn in (range_edge, report_edge):
        with pytest.raises(ValueError):
            fn("garbage", "UTC", end=False)


# ── Fuzz: every GET route, bad values in every parameter ─────────────────────
_DATE_NAMES = {"date", "date_from", "date_to", "from", "from_", "to", "start", "end", "since", "until",
               "day", "month", "start_date", "end_date", "from_date", "to_date"}


def _fuzz_requests():
    """(path, params) pairs for every GET route in the schema: one request per route with every
    string NUL, one with every unbounded integer huge, one per paging param negative, one with every
    date-like param garbage."""
    from main import app
    spec = app.openapi()
    out = []
    for path, ops in spec["paths"].items():
        op = ops.get("get")
        if not op:
            continue
        url = path
        for p in op.get("parameters", []):
            if p["in"] == "path":
                url = url.replace("{" + p["name"] + "}", "00000000-0000-0000-0000-000000000000")
        qs = [p for p in op.get("parameters", []) if p["in"] == "query"]

        def _types(p):
            s = p.get("schema", {})
            alts = s.get("anyOf", [s])
            return {a.get("type") for a in alts}, s, alts

        strs, ints, dates = {}, {}, {}
        for p in qs:
            types, s, alts = _types(p)
            if "integer" in types:
                if not any("maximum" in a or "exclusiveMaximum" in a for a in alts):
                    ints[p["name"]] = HUGE
                if p["name"] in ("skip", "offset"):
                    out.append((url, {p["name"]: -1}))
            elif "string" in types:
                strs[p["name"]] = "a\x00b"
                if p["name"] in _DATE_NAMES or any(a.get("format") in ("date", "date-time") for a in alts):
                    dates[p["name"]] = "garbage"
        out.append((url, strs))
        for name, v in ints.items():
            out.append((url, {name: v}))
        if dates:
            out.append((url, dates))
        if "{" in path:      # NUL in the path itself
            seg = path.split("{", 1)[0] + "a%00b"
            out.append((seg, {}))
    return out


async def test_fuzz_no_get_route_answers_500(client, tenant):
    failures = []
    for url, params in _fuzz_requests():
        r = await client.get(url, params=params, headers=tenant)
        if r.status_code >= 500:
            failures.append((url, params, r.status_code, r.text[:200]))
    assert not failures, f"{len(failures)} 500s, first: {failures[:10]}"
