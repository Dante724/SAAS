"""Request middleware: assigns a request id, logs one access line per request,
and converts unhandled exceptions into a clean 500 (no stack trace leakage).
"""
import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

log = logging.getLogger("repairos.access")


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        request.state.request_id = request_id
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:  # noqa: BLE001 — last line of defence
            dur = (time.perf_counter() - start) * 1000
            log.exception("unhandled error %s %s (%.1fms)",
                          request.method, request.url.path, dur,
                          extra={"request_id": request_id})
            return JSONResponse(
                {"detail": "Internal server error", "request_id": request_id},
                status_code=500,
                headers={"x-request-id": request_id},
            )
        dur = (time.perf_counter() - start) * 1000
        response.headers["x-request-id"] = request_id
        log.info("%s %s -> %s (%.1fms)", request.method, request.url.path,
                 response.status_code, dur, extra={"request_id": request_id})
        return response
