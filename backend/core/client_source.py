"""Which app made a request: Tekly Go on a phone, or the web app.

Tekly Go sends `X-Tekly-Client: go/<version>` on every API call (its api client and its
offline queue). This middleware reads it into a context variable for the length of the
request, and the `client` columns on order_status_logs, order_comments and audit_log default
to it, so every status change, customer message and audit row written by that request
remembers where it came from without each write site having to pass it along.

Values: "go/<version>" for Go, "web" for any other API request (the web app sends nothing),
and None for writes made outside a request (scheduled jobs, scripts).
"""
import re
from contextvars import ContextVar
from typing import Optional

_SAFE = re.compile(r"[a-z][a-z0-9]{0,15}/[A-Za-z0-9._\-]{0,24}")

_client: ContextVar[Optional[str]] = ContextVar("tekly_client", default=None)


def parse_client(header: Optional[str]) -> str:
    """The stored value for a request's X-Tekly-Client header (short and inert, or "web")."""
    h = (header or "").strip()
    if h and _SAFE.fullmatch(h):
        return h[:32]
    return "web"


def current_client() -> Optional[str]:
    """Column default: the client of the request being served (None outside a request)."""
    return _client.get()


def is_go(value: Optional[str]) -> bool:
    return bool(value) and value.startswith("go/")


class ClientSourceMiddleware:
    """Pure ASGI (not BaseHTTPMiddleware) so the context variable is set in the same
    context the endpoint runs in."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope.get("path", "").startswith("/api/"):
            return await self.app(scope, receive, send)
        header = None
        for k, v in scope.get("headers") or []:
            if k == b"x-tekly-client":
                header = v.decode("latin-1")
                break
        token = _client.set(parse_client(header))
        try:
            await self.app(scope, receive, send)
        finally:
            _client.reset(token)
