"""Build 144ct — rate limiter: in-process enforces; Redis path used when a client is present."""
import pytest
from fastapi import HTTPException

from backend.services import ratelimit


def test_in_process_enforces_and_isolates_keys(monkeypatch):
    monkeypatch.setattr(ratelimit, "_redis", False)   # force in-process
    ratelimit._HITS.clear()
    for _ in range(3):
        ratelimit.enforce("t", "ip1", limit=3, window_sec=60)   # 3 allowed
    with pytest.raises(HTTPException) as e:
        ratelimit.enforce("t", "ip1", limit=3, window_sec=60)   # 4th blocked
    assert e.value.status_code == 429
    ratelimit.enforce("t", "ip2", limit=3, window_sec=60)       # different key unaffected


def test_redis_path_used_when_client_present(monkeypatch):
    class FakeRedis:
        def __init__(self):
            self.n = {}
        def incr(self, k):
            self.n[k] = self.n.get(k, 0) + 1
            return self.n[k]
        def expire(self, k, s):
            return None
        def ttl(self, k):
            return 30
    monkeypatch.setattr(ratelimit, "_redis", FakeRedis())
    for _ in range(2):
        ratelimit.enforce("b", "k", limit=2, window_sec=60)
    with pytest.raises(HTTPException) as e:
        ratelimit.enforce("b", "k", limit=2, window_sec=60)
    assert e.value.status_code == 429 and e.value.headers["Retry-After"] == "30"


# ── phase 4: one shared global limiter, Redis retry, bounded memory ───────────
@pytest.mark.asyncio
async def test_global_middleware_counts_through_the_shared_store(client, monkeypatch):
    """The per-IP ceiling goes through ratelimit.enforce, so REDIS_URL shares it across workers."""
    seen = []
    real = ratelimit.enforce

    def spy(bucket, key, limit, window_sec):
        seen.append(bucket)
        return real(bucket, key, limit, window_sec)

    monkeypatch.setattr(ratelimit, "enforce", spy)
    await client.get("/api/auth/me")
    assert "global" in seen


def test_redis_is_retried_after_a_failure(monkeypatch):
    import time as _t

    from backend.config import settings
    monkeypatch.setattr(settings, "REDIS_URL", "redis://127.0.0.1:1/0")
    monkeypatch.setattr(ratelimit, "_redis", False)
    monkeypatch.setattr(ratelimit, "_redis_retry_at", _t.time() - 1)
    attempts = []

    class Boom:
        @staticmethod
        def from_url(*a, **k):
            attempts.append(1)
            raise ConnectionError("down")

    import sys
    import types
    monkeypatch.setitem(sys.modules, "redis", types.SimpleNamespace(Redis=Boom))
    assert ratelimit._get_redis() is None and attempts == [1]
    assert ratelimit._get_redis() is None and attempts == [1]      # backing off
    monkeypatch.setattr(ratelimit, "_redis_retry_at", _t.time() - 1)
    ratelimit._get_redis()
    assert attempts == [1, 1]                                       # retried once due


def test_idle_keys_are_pruned(monkeypatch):
    import time as _t
    monkeypatch.setattr(ratelimit, "_redis", False)
    ratelimit._HITS.clear()
    ratelimit._HITS[("t", "old-ip")].append(_t.time() - 7200)
    ratelimit._HITS[("t", "new-ip")].append(_t.time())
    ratelimit._prune(_t.time())
    assert ("t", "old-ip") not in ratelimit._HITS and ("t", "new-ip") in ratelimit._HITS
