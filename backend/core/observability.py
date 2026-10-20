"""B3 rate limiting + B4 observability — dependency-free middleware.

- Sliding-window in-memory rate limiter (per client IP + path bucket). Good for a
  single-process deploy; swap the store for Redis in multi-process.
- Request metrics (count, latency histogram buckets, status classes) exposed in
  Prometheus text format at /metrics — no prometheus_client dependency.
"""
import time
from collections import defaultdict

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

# ── metrics store ────────────────────────────────────────────────────────────
_REQ_COUNT: dict = defaultdict(int)        # (method, status_class) -> count
_LAT_BUCKETS = (0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)
_LAT_HIST: dict = defaultdict(int)         # bucket_le -> count
_LAT_SUM = [0.0]
_LAT_N = [0]


def render_metrics() -> str:
    lines = ["# HELP repairos_requests_total Total HTTP requests",
             "# TYPE repairos_requests_total counter"]
    for (method, cls), n in sorted(_REQ_COUNT.items()):
        lines.append(f'repairos_requests_total{{method="{method}",status="{cls}"}} {n}')
    lines += ["# HELP repairos_request_latency_seconds Request latency",
              "# TYPE repairos_request_latency_seconds histogram"]
    cumulative = 0
    for b in _LAT_BUCKETS:
        cumulative += _LAT_HIST.get(b, 0)
        lines.append(f'repairos_request_latency_seconds_bucket{{le="{b}"}} {cumulative}')
    lines.append(f'repairos_request_latency_seconds_bucket{{le="+Inf"}} {_LAT_N[0]}')
    lines.append(f"repairos_request_latency_seconds_sum {_LAT_SUM[0]:.4f}")
    lines.append(f"repairos_request_latency_seconds_count {_LAT_N[0]}")
    return "\n".join(lines) + "\n"


def _observe(method: str, status: int, dt: float):
    _REQ_COUNT[(method, f"{status // 100}xx")] += 1
    _LAT_SUM[0] += dt
    _LAT_N[0] += 1
    for b in _LAT_BUCKETS:
        if dt <= b:
            _LAT_HIST[b] += 1


class MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        t0 = time.perf_counter()
        response = await call_next(request)
        _observe(request.method, response.status_code, time.perf_counter() - t0)
        return response


# ── security response headers (144bc) ────────────────────────────────────────
_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "SAMEORIGIN",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(self), microphone=(), geolocation=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    # SPA + inline handlers need 'unsafe-inline'; images/media may be data: URLs.
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "img-src 'self' data: blob:; "
        "font-src 'self' data: https://fonts.gstatic.com; "
        "connect-src 'self'; "
        "frame-ancestors 'self'; "
        "base-uri 'self'; "
        "object-src 'none'"
    ),
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        for k, v in _SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        # Deployed behind TLS: pin HTTPS so a first plain-http visit can't be downgraded.
        # (Browsers ignore HSTS on http responses, so local dev over http is unaffected.)
        from backend.config import settings
        if settings.is_deployed:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response


# ── rate limiter ─────────────────────────────────────────────────────────────
_STATIC_SUFFIXES = (".js", ".mjs", ".css", ".map", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp",
                    ".ico", ".woff", ".woff2", ".ttf", ".webmanifest", ".txt")


def _token_subject(request) -> str | None:
    """Employee id from a validly signed bearer token, else None. Signature is checked so a forged
    token can't buy a fresh budget; expiry/revocation are left to the endpoint's own auth."""
    auth = request.headers.get("authorization") or ""
    if not auth.lower().startswith("bearer "):
        return None
    import jwt

    from backend.config import settings
    try:
        payload = jwt.decode(auth[7:].strip(), settings.SECRET_KEY, algorithms=[settings.ALGORITHM],
                             options={"verify_exp": False})
    except (jwt.PyJWTError, ValueError):
        return None
    sub = payload.get("sub")
    return str(sub) if sub else None


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Global request ceiling. Counts through services.ratelimit, so with REDIS_URL set every
    worker and instance shares one counter (it used to be a per-process dict: N workers
    allowed N x limit, and every IP ever seen stayed in memory).

    Launch QA B5: a shop's staff share one Wi-Fi IP, so a flat per-IP limit that also counted page
    assets locked the whole shop out. Static files are not counted; signed-in traffic is limited
    per user (RATE_LIMIT_USER_PER_MIN); anonymous traffic keeps the per-IP ceiling
    (RATE_LIMIT_IP_PER_MIN)."""
    def __init__(self, app, *, window_s: int = 60,
                 exempt_prefixes=("/metrics", "/health", "/api/health", "/assets/", "/static/")):
        super().__init__(app)
        self.window = window_s
        self.exempt = exempt_prefixes

    async def dispatch(self, request, call_next):
        from fastapi import HTTPException

        from backend.config import settings
        from backend.services import ratelimit
        path = request.url.path
        if any(path.startswith(p) for p in self.exempt) or (
                not path.startswith("/api") and path.lower().endswith(_STATIC_SUFFIXES)):
            return await call_next(request)
        sub = _token_subject(request)
        if sub:
            bucket, key, limit = "global-user", sub, settings.RATE_LIMIT_USER_PER_MIN
        else:
            bucket, key, limit = "global", ratelimit.client_ip(request), settings.RATE_LIMIT_IP_PER_MIN
        try:
            await ratelimit.aenforce(bucket, key, limit, self.window)
        except HTTPException as e:
            return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded"},
                                headers=e.headers or {})
        return await call_next(request)
        try:
            await ratelimit.aenforce("global", ratelimit.client_ip(request), self.limit, self.window)
        except HTTPException as e:
            return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded"},
                                headers=e.headers or {})
        return await call_next(request)
