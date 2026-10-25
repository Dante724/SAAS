"""B3 rate limiting + B4 observability — dependency-free middleware.

- Sliding-window in-memory rate limiter (per user, API key or client IP). Every counted /api
  response carries X-RateLimit-Limit / X-RateLimit-Remaining; a 429 also carries Retry-After. Good for a
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
    """Who a request counts against. Signed-in calls count per user (or API key): staff
    on one shop Wi-Fi, or every client behind a proxy that hides their IP, used to share one bucket
    and lock each other out (launch QA B5). Anonymous calls, and forged or expired tokens, count
    per IP so a made-up token can't buy a fresh budget."""
    from backend.services import ratelimit
    auth = request.headers.get("authorization", "")
    if auth[:7].lower() == "bearer ":
        tok = auth[7:].strip()
        if tok.startswith("rok_"):
            return "k:" + tok               # verified in dispatch (_key_bucket) before it gets its own budget
        try:
            import jwt

            from backend.config import settings
            sub = jwt.decode(tok, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]).get("sub")
            if sub:
                return "u:" + str(sub)
        except Exception:
            pass                                          # bad/expired token: counts by IP
    return "ip:" + ratelimit.client_ip(request)


# Verified API keys: sha256 → (ApiKey.id or None, expires_at). A made-up "rok_…" token must not
# buy a fresh budget, so only a key that exists, is active and has not expired gets its own bucket;
# anything else counts against the caller's IP. The bucket is the key's id (A15: it used to be the
# prefix, which a rotation changes and which is not guaranteed unique). Results are cached briefly
# so a busy integration costs one lookup per minute, and the cache is capped so random tokens
# can't grow it without bound.
_KEY_CACHE: dict[str, tuple[str | None, float]] = {}
_KEY_CACHE_TTL_S = 60.0
_KEY_CACHE_MAX = 5000


def forget_key(token_hash: str | None) -> None:
    """Drop a cached verification (called on revoke and rotate)."""
    if token_hash:
        _KEY_CACHE.pop(token_hash, None)


async def _key_bucket(request, token: str) -> str:
    import time
    from datetime import datetime

    from backend.services import ratelimit
    from backend.services.apikeys import hash_key, key_lookup_clause
    h = hash_key(token)
    now = time.monotonic()
    hit = _KEY_CACHE.get(h)
    if hit and hit[1] > now:
        key_id = hit[0]
    else:
        key_id = None
        try:
            from sqlalchemy import select

            from backend.db.database import get_db
            from backend.models import ApiKey
            dep = request.app.dependency_overrides.get(get_db, get_db)   # the test suite swaps the DB
            agen = dep()
            db = await agen.__anext__()
            try:
                key_id = (await db.execute(select(ApiKey.id).where(
                    key_lookup_clause(h, datetime.utcnow())))).scalars().first()
            finally:
                await agen.aclose()
        except Exception:
            key_id = None
        if len(_KEY_CACHE) >= _KEY_CACHE_MAX:
            _KEY_CACHE.clear()
        _KEY_CACHE[h] = (key_id, now + _KEY_CACHE_TTL_S)
    return ("k:" + key_id) if key_id else ("ip:" + ratelimit.client_ip(request))


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Global ceiling on API calls: per user or API key when signed in (RATE_LIMIT_USER_PER_MIN),
    per IP otherwise (RATE_LIMIT_IP_PER_MIN). Only /api/ is counted: page loads pull dozens of
    assets, and counting those let one busy screen use up a shop's whole budget. Counts through
    services.ratelimit, so with REDIS_URL set every worker and instance shares one counter."""
    def __init__(self, app, *, window_s: int = 60,
                 exempt_prefixes=("/metrics", "/health", "/api/health")):
        super().__init__(app)
        self.window = window_s
        self.exempt = exempt_prefixes

    async def dispatch(self, request, call_next):
        from fastapi import HTTPException

        from backend.config import settings
        from backend.services import ratelimit
        path = request.url.path
        if not path.startswith("/api/") or any(path.startswith(p) for p in self.exempt):
            return await call_next(request)
        key = _rate_key(request)
        if key.startswith("k:"):
            key = await _key_bucket(request, key[2:])
        limit = settings.RATE_LIMIT_IP_PER_MIN if key.startswith("ip:") else settings.RATE_LIMIT_USER_PER_MIN
        try:
            remaining = await ratelimit.aenforce("global", key, limit, self.window)
        except HTTPException as e:
            # A15: say what the limit is and when to come back.
            headers = {**(e.headers or {}), "X-RateLimit-Limit": str(limit), "X-RateLimit-Remaining": "0"}
            headers.setdefault("Retry-After", str(self.window))
            return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded"}, headers=headers)
        response = await call_next(request)
        response.headers.setdefault("X-RateLimit-Limit", str(limit))
        if remaining is not None:
            response.headers.setdefault("X-RateLimit-Remaining", str(remaining))
        return response


# ── public API transport (A11) ───────────────────────────────────────────────
def request_scheme(scope) -> str:
    """The scheme the caller used. With TRUSTED_PROXY_HOPS set, X-Forwarded-Proto is read the
    same way as X-Forwarded-For (the entry our outermost trusted proxy wrote); otherwise the ASGI
    scheme, which uvicorn --proxy-headers already sets from a trusted proxy."""
    from backend.config import settings
    scheme = (scope.get("scheme") or "http").lower()
    hops = int(getattr(settings, "TRUSTED_PROXY_HOPS", 0) or 0)
    if hops <= 0:
        return scheme
    raw = ""
    for k, v in scope.get("headers") or []:
        if k.lower() == b"x-forwarded-proto":
            raw = v.decode("latin-1")
            break
    chain = [p.strip().lower() for p in raw.split(",") if p.strip()]
    if not chain:
        return scheme
    return chain[-hops] if len(chain) >= hops else chain[-1]


class PublicApiTransportMiddleware:
    """Deployed: /api/v1 is refused over plain HTTP (403) instead of quietly working, so an
    integration that sends its key in clear text finds out at once. Dev and test are untouched."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            path = scope.get("path", "")
            if path == "/api/v1" or path.startswith("/api/v1/"):
                from backend.config import settings
                if settings.is_deployed and request_scheme(scope) != "https":
                    resp = JSONResponse(status_code=403, content={
                        "detail": "The Tekly API is only available over HTTPS."})
                    return await resp(scope, receive, send)
        return await self.app(scope, receive, send)
