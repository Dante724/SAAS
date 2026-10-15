"""Request middleware: assigns a request id, logs one access line per request,
and converts unhandled exceptions into a clean 500 (no stack trace leakage).
"""
import logging
import re
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

log = logging.getLogger("repairos.access")

# A caller-supplied id is echoed into headers and every log line for the request, so it must
# be short and inert (no newlines to forge log entries, no megabyte values).
_SAFE_REQUEST_ID = re.compile(r"[A-Za-z0-9._\-]{1,64}")


def _request_id(header: str | None) -> str:
    if header and _SAFE_REQUEST_ID.fullmatch(header):
        return header
    return uuid.uuid4().hex[:12]


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = _request_id(request.headers.get("x-request-id"))
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
