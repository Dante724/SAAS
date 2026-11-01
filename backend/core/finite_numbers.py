"""No NaN or Infinity in any money or quantity input (launch blocker B9).

A part saved with cost NaN returned 201 on Postgres; after that every inventory list returned
500, because JSON can't carry NaN, and the UI showed "No stock yet". Two layers close it:

* `refuse_non_finite_in_models()` makes every pydantic request model refuse NaN/Infinity in
  float and Decimal fields, whether sent as a number, a bare NaN token, or a string like "nan".
  It sets the default on BaseModel itself, so it must run before any backend model is defined
  (backend/__init__.py calls it), and models added later are covered without anyone remembering.
* `NonFiniteJsonMiddleware` refuses a JSON body holding a bare NaN/Infinity token (or a number
  so large it overflows to infinity) before routing, for the few routes that take a plain dict.
"""
import json
import math
import re

from pydantic import BaseModel
from starlette.responses import JSONResponse


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
