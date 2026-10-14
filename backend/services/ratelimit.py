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
_redis = None   # None = untried, False = unavailable, else a client


def _get_redis():
    """Lazily connect once. Returns a live client or None (fall back in-process)."""
    global _redis
    if _redis is not None:
        return _redis or None
    if not getattr(settings, "REDIS_URL", ""):
        _redis = False
        return None
    try:
        import redis
        c = redis.Redis.from_url(settings.REDIS_URL, socket_timeout=0.5,
                                 socket_connect_timeout=0.5)
        c.ping()
        _redis = c
        return c
    except Exception:   # noqa: BLE001 — any failure => in-process fallback
        _redis = False
        return None


def client_ip(request) -> str:
    """Best-effort caller IP (respects a single proxy hop)."""
    fwd = request.headers.get("x-forwarded-for") if request else None
    if fwd:
        return fwd.split(",")[0].strip()
    return getattr(getattr(request, "client", None), "host", None) or "unknown"


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
            pass
    # in-process sliding window (single instance)
    now = time.time()
    dq = _HITS[(bucket, key)]
    cutoff = now - window_sec
    while dq and dq[0] < cutoff:
        dq.popleft()
    if len(dq) >= limit:
        raise _too_many(int(dq[0] + window_sec - now) + 1)
    dq.append(now)
