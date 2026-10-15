"""Rate limiter (sliding window in-process; fixed-window Redis when configured).

Single instance uses an in-memory deque. Set ``REDIS_URL`` and every app
instance shares one counter, so horizontal scaling actually enforces the limit.
Same ``enforce()`` signature either way; callers don't change.
"""
import time
from collections import defaultdict, deque

from fastapi import HTTPException

from backend.config import settings

_HITS: dict = defaultdict(deque)
_redis = None          # None = untried / retry due, False = unavailable, else a client
_redis_retry_at = 0.0  # after a failure, try Redis again at this time (was: never again)
_REDIS_RETRY_S = 30
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
    except Exception:   # noqa: BLE001 — any failure => in-process fallback
        _redis, _redis_retry_at = False, time.time() + _REDIS_RETRY_S
        return None


def _redis_failed():
    global _redis, _redis_retry_at
    _redis, _redis_retry_at = False, time.time() + _REDIS_RETRY_S


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
    return chain[-hops] if len(chain) >= hops else (chain[0] if chain else peer)


def _too_many(retry: int) -> HTTPException:
    return HTTPException(429, "Too many attempts — please slow down and try again shortly.",
                         headers={"Retry-After": str(max(1, retry))})


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
        except Exception:   # noqa: BLE001 — Redis hiccup => fall back to in-process
            _redis_failed()
    # in-process sliding window (single instance)
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
