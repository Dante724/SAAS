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
