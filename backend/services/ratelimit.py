"""Rate limiter (sliding window in-process; fixed-window Redis when configured).

Single instance uses an in-memory deque. Set ``REDIS_URL`` and every app
instance shares one counter, so horizontal scaling actually enforces the limit.
If Redis is configured but unreachable the limiter counts per process until the next
retry (``_REDIS_RETRY_S``): it says so with one WARNING per retry window, and
``store_status()`` reports which store is counting (shown by ``GET /api/readyz``).

``aenforce()`` is what request handlers and middleware use: redis.asyncio, one pipelined
round trip, never blocks the event loop. ``enforce()`` is the same rule for sync callers
(sync redis client). Same signature and same in-process fallback either way.
"""
import logging
import time
from collections import defaultdict, deque

from fastapi import HTTPException

from backend.config import settings

log = logging.getLogger("tekly.ratelimit")
_HITS: dict = defaultdict(deque)
_redis = None          # None = untried / retry due, False = unavailable, else a client
_redis_retry_at = 0.0  # after a failure, try Redis again at this time (was: never again)
_REDIS_RETRY_S = 30
_aredis = None         # async client: same states as _redis
_aredis_retry_at = 0.0
_calls = 0
_PRUNE_EVERY = 1000


def _get_redis():
    """Connect lazily. Returns a live client or None (fall back in-process). A failure is
    retried after _REDIS_RETRY_S instead of disabling the shared store for the process lifetime."""
    global _redis, _redis_retry_at
    if _redis:
        return _redis
    if not getattr(settings, "REDIS_URL", ""):
        return None
    if _redis is False and time.time() < _redis_retry_at:
        return None
    try:
        import redis
        c = redis.Redis.from_url(settings.REDIS_URL, socket_timeout=0.5,
                                 socket_connect_timeout=0.5)
        c.ping()
        _redis = c
        return c
    except Exception as exc:   # noqa: BLE001 — any failure => in-process fallback
        _redis_failed(exc)
        return None


def _redis_failed(exc: BaseException | None = None, *, asynchronous: bool = False) -> None:
    """Fall back to per-process counting until the next retry, and say so — once per retry
    window, since the next attempt is only made after _REDIS_RETRY_S. The URL is not logged
    (it may carry a password); the driver's message names only host and port."""
    global _redis, _redis_retry_at, _aredis, _aredis_retry_at
    if asynchronous:
        _aredis, _aredis_retry_at = False, time.time() + _REDIS_RETRY_S
    else:
        _redis, _redis_retry_at = False, time.time() + _REDIS_RETRY_S
    log.warning("rate limits: Redis (REDIS_URL) is unreachable — %s: %s. Counting per-process for the next "
                "%ss; with several workers each one enforces its own copy of every limit.",
                type(exc).__name__ if exc else "error", exc, _REDIS_RETRY_S)


async def store_status() -> str:
    """Which store is counting right now, for readiness: "redis", "local" (no REDIS_URL), or
    "local (redis unreachable)" (REDIS_URL set but the last attempt failed; retried after
    _REDIS_RETRY_S). Readiness stays "ready" either way: a rate-limit store outage must not take
    the instance out of rotation."""
    if not getattr(settings, "REDIS_URL", ""):
        return "local"
    return "redis" if await _get_aredis() is not None else "local (redis unreachable)"


def _prune(now: float) -> None:
    """Drop keys with no hits in the last hour — one entry per IP/user ever seen otherwise
    stays in memory for the life of the worker."""
    for k in [k for k, dq in _HITS.items() if not dq or dq[-1] < now - 3600]:
        _HITS.pop(k, None)


def client_ip(request) -> str:
    """Caller IP for rate limiting. X-Forwarded-For is client-writable, so only the hops our
    own proxies appended are trusted (settings.TRUSTED_PROXY_HOPS); never the left-most entry,
    which an attacker can rotate on every request to dodge limits."""
    peer = getattr(getattr(request, "client", None), "host", None) or "unknown"
    hops = int(getattr(settings, "TRUSTED_PROXY_HOPS", 0) or 0)
    if hops <= 0 or request is None:
        return peer
    chain = [p.strip() for p in (request.headers.get("x-forwarded-for") or "").split(",") if p.strip()]
    # A chain shorter than the configured hops did not come through all our proxies, so none
    # of it can be trusted (its left-most entry is whatever the client wrote): use the peer.
    return chain[-hops] if len(chain) >= hops else peer


def _too_many(retry: int) -> HTTPException:
    return HTTPException(429, "Too many attempts — please slow down and try again shortly.",
                         headers={"Retry-After": str(max(1, retry))})


async def _get_aredis():
    """Async twin of _get_redis (redis.asyncio): lazily connected, retried after a failure."""
    global _aredis, _aredis_retry_at
    if _aredis:
        return _aredis
    if not getattr(settings, "REDIS_URL", ""):
        return None
    if _aredis is False and time.time() < _aredis_retry_at:
        return None
    try:
        import redis.asyncio as aioredis
        c = aioredis.Redis.from_url(settings.REDIS_URL, socket_timeout=0.5,
                                    socket_connect_timeout=0.5)
        await c.ping()
        _aredis = c
        return c
    except Exception as exc:   # noqa: BLE001 — any failure => in-process fallback
        _redis_failed(exc, asynchronous=True)
        return None


def aredis_failed() -> None:
    """Mark the shared async client down (after a failed call): callers fall back in-process
    and the connection is retried after _REDIS_RETRY_S. Used by other Redis users (the reports
    dashboard cache) so one outage is handled the same way everywhere."""
    global _aredis, _aredis_retry_at
    _aredis, _aredis_retry_at = False, time.time() + _REDIS_RETRY_S


async def get_aredis():
    """The shared redis.asyncio client, or None (no REDIS_URL, or Redis down: fall back)."""
    return await _get_aredis()


async def aenforce(bucket: str, key: str, limit: int, window_sec: int) -> int:
    """Async enforce(): allow `limit` events per `window_sec` for (bucket, key); else 429.
    Returns how many events are left in the current window (for X-RateLimit-Remaining).
    Redis: SET NX EX (start the window) + INCR + TTL in one pipeline — one round trip, and the
    expiry is set atomically with the key, so a crash between calls can't leave a key that
    never expires."""
    r = await _get_aredis()
    if r is not None:
        try:
            rk = f"rl:{bucket}:{key}"
            async with r.pipeline(transaction=True) as p:
                p.set(rk, 0, ex=window_sec, nx=True)
                p.incr(rk)
                p.ttl(rk)
                _, n, ttl = await p.execute()
            if n > limit:
                raise _too_many(ttl if ttl and ttl > 0 else window_sec)
            return max(0, limit - int(n))
        except HTTPException:
            raise
        except Exception as exc:   # noqa: BLE001 — Redis hiccup => fall back to in-process
            _redis_failed(exc, asynchronous=True)
    return _local_enforce(bucket, key, limit, window_sec)


def enforce(bucket: str, key: str, limit: int, window_sec: int) -> None:
    """Allow `limit` events per `window_sec` for (bucket, key); else raise 429."""
    r = _get_redis()
    if r is not None:
        try:
            rk = f"rl:{bucket}:{key}"
            n = r.incr(rk)
            if n == 1:
                r.expire(rk, window_sec)   # ponytail: fixed window (not sliding); fine for brute-force blunting
            if n > limit:
                raise _too_many(r.ttl(rk) or window_sec)
            return
        except HTTPException:
            raise
        except Exception as exc:   # noqa: BLE001 — Redis hiccup => fall back to in-process
            _redis_failed(exc)
    _local_enforce(bucket, key, limit, window_sec)


def _local_enforce(bucket: str, key: str, limit: int, window_sec: int) -> int:
    """In-process sliding window (single instance). Returns the events left in the window."""
    global _calls
    now = time.time()
    _calls += 1
    if _calls % _PRUNE_EVERY == 0:
        _prune(now)
    dq = _HITS[(bucket, key)]
    cutoff = now - window_sec
    while dq and dq[0] < cutoff:
        dq.popleft()
    if len(dq) >= limit:
        raise _too_many(int(dq[0] + window_sec - now) + 1)
    dq.append(now)
    return max(0, limit - len(dq))
