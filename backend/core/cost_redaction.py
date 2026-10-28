"""Strip cost and margin fields from staff API responses for roles that can't see costs.

Runs after the endpoint. Owners (no role) and public/customer routes are untouched.

Batch 2 (Oct 2026 audit): this used to be a BaseHTTPMiddleware that buffered and re-parsed
every staff JSON response and opened a second DB session to look the role up. Now it is a
plain ASGI middleware and the decision is made once per request, before any body bytes flow:

- get_current_employee loads the employee's role in the same query and records
  ``request.state.can_see_costs``; other auth paths fall back to a short per-role cache.
- A viewer who may see costs streams straight through — nothing is buffered or parsed.
- Only a response for a restricted viewer is buffered, and it is always redacted whatever
  its size (fail closed: skipping a large body would leak the costs in it).

See backend/services/redact.py for the field list and the write-side rule."""
import json
import time

_ROLE_TTL_S = 30
_role_cache: dict = {}     # role_id -> (expires_at, can_see_costs); fallback path only


def invalidate_role(role_id=None) -> None:
    """Forget cached decisions (all, or one role) — called when a role's permissions change."""
    if role_id is None:
        _role_cache.clear()
    else:
        _role_cache.pop(role_id, None)


async def _role_can_see_costs(role_id: str) -> bool:
    hit = _role_cache.get(role_id)
    now = time.monotonic()
    if hit and hit[0] > now:
        return hit[1]
    from sqlalchemy import select

    from backend.db.database import AsyncSessionLocal
    from backend.models import Role
    from backend.services.permissions import role_allows
    from backend.services.redact import COST_PERM
    async with AsyncSessionLocal() as db:
        role = (await db.execute(select(Role).where(Role.id == role_id))).scalar_one_or_none()
    ok = role is not None and role_allows(role, COST_PERM)
    if len(_role_cache) > 10_000:
        _role_cache.clear()
    _role_cache[role_id] = (now + _ROLE_TTL_S, ok)
    return ok


async def _viewer_can_see_costs(state: dict) -> bool:
    if not state.get("employee_id"):
        return True                       # not signed in as staff
    decided = state.get("can_see_costs")
    if decided is not None:
        return bool(decided)
    if not state.get("employee_role_id"):
        return True                       # the owner (auth decides a roleless non-owner above)
    return await _role_can_see_costs(state["employee_role_id"])


class CostRedactionMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "") if scope["type"] == "http" else ""
        if not path.startswith("/api/") or path.startswith(("/api/public", "/api/auth/")):
            return await self.app(scope, receive, send)
        # request.state is backed by scope["state"]; create it now so the endpoint's writes
        # (employee id, role, decision) land in the dict this middleware reads afterwards.
        state = scope.setdefault("state", {})
        start = None
        chunks: list = []

        async def send_wrapper(message):
            nonlocal start
            if message["type"] == "http.response.start":
                ctype = b""
                for k, v in message.get("headers") or []:
                    if k.lower() == b"content-type":
                        ctype = v
                        break
                if b"application/json" in ctype and not await _viewer_can_see_costs(state):
                    start = message          # hold the head until the body is redacted
                    return
                return await send(message)
            if start is None or message["type"] != "http.response.body":
                return await send(message)
            chunks.append(message.get("body", b""))
            if message.get("more_body"):
                return
            from backend.services.redact import mentions_costs, redact
            body = b"".join(chunks)
            if mentions_costs(body):
                try:
                    body = json.dumps(redact(json.loads(body))).encode()
                except ValueError:
                    pass
            headers = [(k, v) for k, v in (start.get("headers") or []) if k.lower() != b"content-length"]
            headers.append((b"content-length", str(len(body)).encode()))
            await send({**start, "headers": headers})
            await send({"type": "http.response.body", "body": body})

        await self.app(scope, receive, send_wrapper)
