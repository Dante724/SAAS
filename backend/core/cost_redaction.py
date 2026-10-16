"""Strip cost and margin fields from staff API responses for roles that can't see costs.

Runs after the endpoint. Owners (no role) and public/customer routes are untouched, and the
role is only looked up when the response actually contains a cost field, so most requests
pay nothing. See backend/services/redact.py for the field list and the write-side rule."""
import json

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response


class CostRedactionMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        resp = await call_next(request)
        path = request.url.path
        if not path.startswith("/api/") or path.startswith(("/api/public", "/api/auth/")):
            return resp
        if "application/json" not in (resp.headers.get("content-type") or ""):
            return resp
        role_id = getattr(request.state, "employee_role_id", None)
        if not getattr(request.state, "employee_id", None) or not role_id:
            return resp                      # not signed in as staff, or the owner
        body = b"".join([chunk async for chunk in resp.body_iterator])
        from backend.services.redact import COST_PERM, mentions_costs, redact
        if mentions_costs(body):
            from sqlalchemy import select

            from backend.db.database import AsyncSessionLocal
            from backend.models import Role
            from backend.services.permissions import role_allows
            async with AsyncSessionLocal() as db:
                role = (await db.execute(select(Role).where(Role.id == role_id))).scalar_one_or_none()
            if role is None or not role_allows(role, COST_PERM):
                try:
                    body = json.dumps(redact(json.loads(body))).encode()
                except ValueError:
                    pass
        headers = {k: v for k, v in resp.headers.items() if k.lower() != "content-length"}
        return Response(content=body, status_code=resp.status_code, headers=headers)
