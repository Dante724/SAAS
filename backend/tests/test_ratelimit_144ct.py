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
    real = ratelimit.aenforce

    async def spy(bucket, key, limit, window_sec):
        seen.append(bucket)
        return await real(bucket, key, limit, window_sec)

    monkeypatch.setattr(ratelimit, "aenforce", spy)
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


# ── batch 2: async limiter — redis.asyncio, one pipelined round trip ─────────
@pytest.mark.asyncio
async def test_async_redis_path_is_one_pipeline(monkeypatch):
    class Pipe:
        def __init__(self, store, log):
            self.store, self.log, self.ops = store, log, []
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        def set(self, k, v, ex=None, nx=False):
            self.ops.append(("set", k, v, ex, nx))
        def incr(self, k):
            self.ops.append(("incr", k))
        def ttl(self, k):
            self.ops.append(("ttl", k))
        async def execute(self):
            self.log.append([o[0] for o in self.ops])
            out = []
            for o in self.ops:
                if o[0] == "set":
                    out.append(None if o[1] in self.store else self.store.setdefault(o[1], o[2]) is not None)
                elif o[0] == "incr":
                    self.store[o[1]] += 1
                    out.append(self.store[o[1]])
                else:
                    out.append(42)
            return out

    class FakeAsyncRedis:
        def __init__(self):
            self.store, self.log = {}, []
        def pipeline(self, transaction=True):
            return Pipe(self.store, self.log)

    fake = FakeAsyncRedis()
    monkeypatch.setattr(ratelimit, "_aredis", fake)
    for _ in range(2):
        await ratelimit.aenforce("b", "k", limit=2, window_sec=60)
    with pytest.raises(HTTPException) as e:
        await ratelimit.aenforce("b", "k", limit=2, window_sec=60)
    assert e.value.status_code == 429 and e.value.headers["Retry-After"] == "42"
    assert fake.log == [["set", "incr", "ttl"]] * 3          # one round trip per check


@pytest.mark.asyncio
async def test_async_falls_back_in_process_when_redis_breaks(monkeypatch):
    class Broken:
        def pipeline(self, transaction=True):
            raise ConnectionError("down")
    monkeypatch.setattr(ratelimit, "_aredis", Broken())
    ratelimit._HITS.clear()
    await ratelimit.aenforce("f", "k", limit=1, window_sec=60)
    assert ratelimit._aredis is False                          # backs off, retried later
    with pytest.raises(HTTPException):
        await ratelimit.aenforce("f", "k", limit=1, window_sec=60)
