"""No NUL characters in request text (final review QA-2).

Postgres text can't hold U+0000: asyncpg raises CharacterNotInRepertoireError (SQLSTATE 22021) the
moment one reaches SQL, as a filter (`?search=%00`) or a saved value (`{"name": "A\\u0000B"}`).
Fuzzing found 55 GET routes, the main create endpoints and one public route answering 500. No
screen sends a NUL on purpose, so `NulByteMiddleware` refuses one on any /api route with a 422
before routing, in:

* the query string (`%00`, or a raw NUL byte),
* the path (`/api/clients/%00`),
* a JSON body string or key (the escape `\\u0000`; a raw NUL byte is already invalid JSON).

File uploads (multipart) and other binary bodies are left alone. The DB error handler maps 22021
to a 422 as the backstop for any other way in (core/db_errors.py).
"""
import json
import re

from starlette.responses import JSONResponse

MESSAGE = "Text can't contain NUL characters (\\u0000)."

# Cheap pre-check: an escaped NUL anywhere in the body. Only then is the body parsed, so that the
# six characters "\\u0000" inside a string (an escaped backslash, then "u0000") are not refused.
_ESCAPED_NUL = re.compile(rb"\\u0000")


def _has_nul(value) -> bool:
    if isinstance(value, str):
        return "\x00" in value
    if isinstance(value, dict):
        return any(_has_nul(k) or _has_nul(v) for k, v in value.items())
    if isinstance(value, list):
        return any(_has_nul(v) for v in value)
    return False


def json_has_nul(body: bytes) -> bool:
    if not _ESCAPED_NUL.search(body):
        return False
    try:
        return _has_nul(json.loads(body))
    except ValueError:
        return False      # not valid JSON: the route's own parsing reports it


def url_has_nul(scope) -> bool:
    qs = scope.get("query_string") or b""
    if b"\x00" in qs or b"%00" in qs:
        return True
    raw = scope.get("raw_path") or b""
    return "\x00" in (scope.get("path") or "") or b"%00" in raw


class NulByteMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        # Only the API reaches the database; page and file routes keep their own 404s for odd paths.
        if scope["type"] != "http" or not (scope.get("path") or "").startswith("/api"):
            return await self.app(scope, receive, send)
        if url_has_nul(scope):
            return await JSONResponse({"detail": MESSAGE}, 422)(scope, receive, send)
        if scope.get("method") not in ("POST", "PUT", "PATCH"):
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
        if json_has_nul(b"".join(chunks)):
            return await JSONResponse({"detail": MESSAGE}, 422)(scope, receive, send)

        async def replay():
            return messages.pop(0) if messages else await receive()

        await self.app(scope, replay, send)
