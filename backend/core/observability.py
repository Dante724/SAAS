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
    # Nothing the app serves belongs in a search index: staff screens, and /track, /portal and
    # /pay pages whose URL is the customer's key.
    "X-Robots-Tag": "noindex, nofollow",
    # No inline script anywhere except the platform-admin page, which sends ADMIN_CSP itself, so a
    # markup injection can't run code. Styles stay inline-friendly; media may be data: URLs.
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; "
        "font-src 'self' data:; "
        "connect-src 'self'; "
        "frame-ancestors 'self'; "
        "base-uri 'self'; "
        "object-src 'none'"
    ),
}


# /admin still has inline handlers (onclick=…); it renders escaped data only. Route-set headers win
# over the defaults above (setdefault).
ADMIN_CSP = _SECURITY_HEADERS["Content-Security-Policy"].replace("script-src 'self';", "script-src 'self' 'unsafe-inline';")


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
def _rate_key(request) -> str:
    """Who a request counts against. Signed-in calls count per user (or API key): staff on one
    shop Wi-Fi, or every client behind a proxy that hides their IP, used to share one bucket and
    lock each other out. Anonymous calls count per IP."""
    from backend.services import ratelimit
    auth = request.headers.get("authorization", "")
    if auth[:7].lower() == "bearer ":
        tok = auth[7:].strip()
        if tok.startswith("rok_"):
            return "k:" + tok[:12]                       # API key prefix (rok_<8 hex>)
        try:
            import jwt

            from backend.config import settings
            sub = jwt.decode(tok, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]).get("sub")
            if sub:
                return "u:" + str(sub)
        except Exception:
            pass                                          # bad/expired token: counts by IP
    return "ip:" + ratelimit.client_ip(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Global ceiling on API calls, per user when signed in, per IP otherwise (_rate_key).
    Only /api/ is counted: page loads pull dozens of assets, and counting those let one busy
    screen use up a shop's whole budget. Counts through services.ratelimit, so with REDIS_URL
    set every worker and instance shares one counter."""
    def __init__(self, app, *, limit: int = 300, window_s: int = 60,
                 exempt_prefixes=("/metrics", "/health", "/api/health")):
        super().__init__(app)
        self.limit = limit
        self.window = window_s
        self.exempt = exempt_prefixes

    async def dispatch(self, request, call_next):
        from fastapi import HTTPException

        from backend.services import ratelimit
        path = request.url.path
        if not path.startswith("/api/") or any(path.startswith(p) for p in self.exempt):
            return await call_next(request)
        try:
            await ratelimit.aenforce("global", _rate_key(request), self.limit, self.window)
        except HTTPException as e:
            return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded"},
                                headers=e.headers or {})
        return await call_next(request)
