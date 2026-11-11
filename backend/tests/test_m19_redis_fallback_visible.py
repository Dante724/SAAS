"""M19 — when Redis is configured but unreachable, the fallback to per-process limits is visible.

With REDIS_URL set and the server down, every rate limit silently became per-worker (N workers =
N times the limit) with no log line and nothing in readiness. Now the fallback logs one WARNING per
retry window and /api/readyz reports which store is counting. Readiness itself stays "ready": a
rate-limit store outage must not take the whole instance out of rotation (owner decision).
"""
import logging
import time

import pytest

from backend.config import settings
from backend.services import ratelimit

CLOSED_PORT = "redis://127.0.0.1:1/0"     # nothing listens on port 1: connection refused at once


@pytest.fixture
def redis_down(monkeypatch):
    monkeypatch.setattr(settings, "REDIS_URL", CLOSED_PORT)
    for name in ("_redis", "_aredis"):
        monkeypatch.setattr(ratelimit, name, None)              # untried: the next call connects
    monkeypatch.setattr(ratelimit, "_redis_retry_at", 0.0)
    monkeypatch.setattr(ratelimit, "_aredis_retry_at", 0.0)


@pytest.fixture
def redis_unset(monkeypatch):
    monkeypatch.setattr(settings, "REDIS_URL", "")
    monkeypatch.setattr(ratelimit, "_redis", None)
    monkeypatch.setattr(ratelimit, "_aredis", None)


def _warnings(caplog):
    return [r for r in caplog.records if r.levelno == logging.WARNING and "redis" in r.getMessage().lower()]


@pytest.mark.asyncio
async def test_unreachable_redis_logs_one_warning_per_retry_window(redis_down, caplog):
    caplog.set_level(logging.WARNING, logger="tekly.ratelimit")
    for _ in range(3):
        assert await ratelimit.aenforce("m19", "k", 5, 60) >= 0        # still enforced, per process
    assert len(_warnings(caplog)) == 1, [r.getMessage() for r in caplog.records]
    assert "per-process" in _warnings(caplog)[0].getMessage()
    # The next retry window reconnects, fails again, and says so again — once.
    ratelimit._aredis_retry_at = time.time() - 1
    await ratelimit.aenforce("m19", "k", 5, 60)
    await ratelimit.aenforce("m19", "k", 5, 60)
    assert len(_warnings(caplog)) == 2


def test_sync_enforce_warns_the_same_way(redis_down, caplog):
    caplog.set_level(logging.WARNING, logger="tekly.ratelimit")
    ratelimit.enforce("m19s", "k", 5, 60)
    ratelimit.enforce("m19s", "k", 5, 60)
    assert len(_warnings(caplog)) == 1


@pytest.mark.asyncio
async def test_readyz_reports_the_rate_limit_store(client, redis_down):
    r = await client.get("/api/readyz")
    assert r.status_code == 200 and r.json()["status"] == "ready"        # not failed: owner decision
    assert r.json()["rate_limit_store"] == "local (redis unreachable)"


@pytest.mark.asyncio
async def test_readyz_says_local_when_no_redis_is_configured(client, redis_unset):
    r = await client.get("/api/readyz")
    assert r.status_code == 200 and r.json()["rate_limit_store"] == "local"


@pytest.mark.asyncio
async def test_readyz_says_redis_when_the_shared_store_is_up(client, monkeypatch):
    class Pipe:                      # the one pipelined round trip aenforce() makes (SET NX, INCR, TTL)
        def __init__(self, store):
            self.store, self.ops = store, []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def set(self, k, v, ex=None, nx=False):
            self.ops.append(("set", k))

        def incr(self, k):
            self.ops.append(("incr", k))

        def ttl(self, k):
            self.ops.append(("ttl", k))

        async def execute(self):
            out = []
            for op, k in self.ops:
                if op == "incr":
                    self.store[k] = self.store.get(k, 0) + 1
                out.append(self.store.get(k) if op == "incr" else 60)
            return out

    class LiveRedis:
        def __init__(self):
            self.store = {}

        async def ping(self):
            return True

        def pipeline(self, transaction=True):
            return Pipe(self.store)
    monkeypatch.setattr(settings, "REDIS_URL", "redis://example.invalid/0")
    monkeypatch.setattr(ratelimit, "_aredis", LiveRedis())
    r = await client.get("/api/readyz")
    assert r.status_code == 200 and r.json()["rate_limit_store"] == "redis"
