"""No NaN or Infinity in any money or quantity input (launch blocker B9).

A part saved with cost NaN returned 201 on Postgres; after that every inventory list returned
500, because JSON can't carry NaN, and the UI showed "No stock yet". Two layers close it:

* `refuse_non_finite_in_models()` makes every pydantic request model refuse NaN/Infinity in
  float and Decimal fields, whether sent as a number, a bare NaN token, or a string like "nan".
  It sets the default on BaseModel itself, so it must run before any backend model is defined
  (backend/__init__.py calls it), and models added later are covered without anyone remembering.
* `NonFiniteJsonMiddleware` refuses a JSON body holding a bare NaN/Infinity token (or a number
  so large it overflows to infinity) before routing, for the few routes that take a plain dict.

Query-string floats don't go through BaseModel, so each one sets `allow_inf_nan=False` itself
(a test walks every route). A finite number too large to round to cents (1e307) made money.q2
raise decimal.InvalidOperation: `too_large_number_handler` answers that with a 422, not a 500.
Last line of defence: `_refuse_non_finite_writes` refuses to flush NaN/Infinity into any float
or numeric column (a number that reached it some other way, e.g. a sum that overflowed).

Values stored before the fix are recovered without SQL: `FiniteJSONResponse` (the app's default
response) sends a stored NaN/Infinity as null, so the list loads and the user can correct the
record, and `clear_non_finite` (run by a migration) clears the ones already in the database.
"""
import json
import logging
import math
import re
from decimal import Decimal
from functools import cache

from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import event, inspect
from sqlalchemy.orm import Session
from sqlalchemy.types import Float, Numeric
from starlette.responses import JSONResponse

log = logging.getLogger("tekly.finite_numbers")


def refuse_non_finite_in_models() -> None:
    BaseModel.model_config["allow_inf_nan"] = False


# Cheap pre-check so ordinary bodies are never parsed twice: the bare tokens, or a 3-digit exponent.
_SUSPECT = re.compile(rb"NaN|Infinity|[eE]\+?\d{3}")


class _NonFinite(ValueError):
    pass


def _refuse(token):
    raise _NonFinite(token)


def _finite_float(s: str) -> float:
    f = float(s)
    if not math.isfinite(f):
        raise _NonFinite(s)
    return f


def has_non_finite(body: bytes) -> bool:
    if not _SUSPECT.search(body):
        return False
    try:
        json.loads(body, parse_constant=_refuse, parse_float=_finite_float)
    except _NonFinite:
        return True
    except ValueError:
        return False      # not valid JSON: the route's own parsing reports it
    return False


class NonFiniteJsonMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") not in ("POST", "PUT", "PATCH"):
            return await self.app(scope, receive, send)
        ctype = next((v for k, v in scope.get("headers") or () if k == b"content-type"), b"")
        if b"json" not in ctype:
            return await self.app(scope, receive, send)

        messages, chunks = [], []
        while True:
            msg = await receive()
            messages.append(msg)
            if msg["type"] != "http.request":
                break
            chunks.append(msg.get("body") or b"")
            if not msg.get("more_body"):
                break
        if has_non_finite(b"".join(chunks)):
            return await JSONResponse(
                {"detail": "Numbers must be real values: NaN and Infinity aren't allowed."}, 422,
            )(scope, receive, send)

        async def replay():
            return messages.pop(0) if messages else await receive()

        await self.app(scope, replay, send)


class NonFiniteValue(HTTPException):
    def __init__(self, column: str):
        super().__init__(422, "Numbers must be real values: NaN and Infinity aren't allowed.")
        self.column = column


@cache
def _number_attrs(mapper) -> tuple:
    return tuple(a.key for a in mapper.column_attrs
                 if len(a.columns) == 1 and isinstance(a.columns[0].type, (Float, Numeric)))


def _non_finite(v) -> bool:
    if isinstance(v, Decimal):
        return not v.is_finite()
    return isinstance(v, float) and not math.isfinite(v)


@event.listens_for(Session, "before_flush")
def _refuse_non_finite_writes(session: Session, flush_context, instances) -> None:
    # session.new builds a fresh set on every access: read it once, or a 33k-row import flush
    # would rebuild it per object (quadratic).
    new = list(session.new)
    for obj, is_new in [(o, True) for o in new] + [(o, False) for o in session.dirty]:
        state = inspect(obj)
        for key in _number_attrs(state.mapper):
            vals = [getattr(obj, key, None)] if is_new else state.attrs[key].history.added
            if any(_non_finite(v) for v in vals):
                raise NonFiniteValue(f"{state.mapper.local_table.name}.{key}")


async def too_large_number_handler(request, exc):
    # Mostly a finite number too large to round to cents; any other uncaught decimal error is
    # logged so a real bug isn't hidden behind the 422.
    log.warning("decimal.InvalidOperation on %s %s: %r", request.method, request.url.path, exc)
    return JSONResponse({"detail": "That number is too large or isn't a valid number."}, 422)


def _finite(v):
    if isinstance(v, float):
        return v if math.isfinite(v) else None
    if isinstance(v, dict):
        return {k: _finite(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_finite(x) for x in v]
    return v


class FiniteJSONResponse(JSONResponse):
    def render(self, content) -> bytes:
        try:
            return super().render(content)
        except ValueError:     # "Out of range float values are not JSON compliant"
            log.warning("a response held NaN/Infinity (stored before B9?); sent as null")
            return super().render(_finite(content))


def clear_non_finite(bind) -> int:
    """Set every stored NaN/Infinity in a float or numeric column to NULL (0 where the column
    is NOT NULL). Returns how many rows changed. Postgres numeric and float columns can hold
    NaN and Infinity; SQLite stores NaN as NULL but keeps Infinity."""
    dialect = bind.dialect.name
    if dialect == "postgresql":
        def bad(c):
            return f"{c}::text IN ('NaN', 'Infinity', '-Infinity')"
    elif dialect == "sqlite":
        def bad(c):
            return f"({c} > 1e308 OR {c} < -1e308)"
    else:
        return 0
    insp, q, changed = inspect(bind), bind.dialect.identifier_preparer.quote, 0
    for table in insp.get_table_names():
        cols = [c for c in insp.get_columns(table) if isinstance(c["type"], (Float, Numeric))]
        if not cols:
            continue
        sets = ", ".join(f"{q(c['name'])} = CASE WHEN {bad(q(c['name']))} THEN "
                         f"{'NULL' if c['nullable'] else '0'} ELSE {q(c['name'])} END" for c in cols)
        where = " OR ".join(bad(q(c["name"])) for c in cols)
        n = bind.exec_driver_sql(f"UPDATE {q(table)} SET {sets} WHERE {where}").rowcount or 0
        if n:
            log.warning("cleared NaN/Infinity in %s row(s) of %s", n, table)
        changed += n
    return changed
