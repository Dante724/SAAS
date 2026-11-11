"""Two things that let the app run more than one worker cleanly:

1. Slow-query logging: a statement slower than SLOW_QUERY_MS logs one WARNING (duration, the
   statement's start, the request id; never the bound values) and counts in /metrics.
2. The reports dashboard cache lives in Redis when REDIS_URL is set (shared by every worker),
   falling back to the in-process dict when Redis is absent or failing, never failing the request.

The clock is patched so "slow" is deterministic on SQLite and Postgres alike.
"""
import json
import logging
import re

import pytest
from sqlalchemy import text

from backend.config import settings
from backend.core import observability
from backend.routers import reports
from backend.services import ratelimit

SLOW_LOGGER = "tekly.db.slow"


def _slow_total() -> int:
    m = re.search(r"^repairos_slow_queries_total (\d+)$", observability.render_metrics(), re.M)
    return int(m.group(1)) if m else -1


@pytest.fixture
def slow_clock(monkeypatch):
    """Every clock read is one second after the last: every statement takes 1000 ms."""
    t = [1000.0]

    def tick():
        t[0] += 1.0
        return t[0]

    monkeypatch.setattr(observability, "_clock", tick, raising=False)
    return tick


def _slow_records(caplog):
    return [r for r in caplog.records if r.name == SLOW_LOGGER and r.levelno == logging.WARNING]


@pytest.mark.asyncio
async def test_slow_statement_logs_warning_without_parameters_and_counts(db, slow_clock, monkeypatch, caplog):
    monkeypatch.setattr(settings, "SLOW_QUERY_MS", 1)
    caplog.set_level(logging.WARNING, logger=SLOW_LOGGER)
    before = _slow_total()
    pad = " AS e" + "".join(f", {i} AS pad_col_{i}" for i in range(30)) + ", 1 AS tail_marker_xyz"
    await db.execute(text("SELECT :email" + pad), {"email": "pii-leak@example.com"})

    recs = _slow_records(caplog)
    assert recs, "no slow-query WARNING was logged"
    msg = recs[-1].getMessage()
    assert "SELECT" in msg and "ms" in msg
    assert "1000" in msg                                   # the duration
    assert "pii-leak" not in msg and "example.com" not in msg   # bound values never logged
    assert "tail_marker_xyz" not in msg                    # statement is truncated
    assert _slow_total() >= max(before, 0) + 1


@pytest.mark.asyncio
async def test_threshold_zero_disables(db, slow_clock, monkeypatch, caplog):
    monkeypatch.setattr(settings, "SLOW_QUERY_MS", 0)
    caplog.set_level(logging.WARNING, logger=SLOW_LOGGER)
    before = _slow_total()
    await db.execute(text("SELECT 1"))
    assert not _slow_records(caplog)
    assert _slow_total() == before


@pytest.mark.asyncio
async def test_fast_statement_not_logged(db, monkeypatch, caplog):
    monkeypatch.setattr(settings, "SLOW_QUERY_MS", 60_000)
    caplog.set_level(logging.WARNING, logger=SLOW_LOGGER)
    await db.execute(text("SELECT 1"))
    assert not _slow_records(caplog)


@pytest.mark.asyncio
async def test_slow_query_carries_the_request_id(client, tenant, slow_clock, monkeypatch, caplog):
    monkeypatch.setattr(settings, "SLOW_QUERY_MS", 1)
    caplog.set_level(logging.WARNING, logger=SLOW_LOGGER)
    r = await client.get("/api/reports/dashboard", headers={**tenant, "x-request-id": "req-slow-42"})
    assert r.status_code == 200
    recs = _slow_records(caplog)
    assert recs, "no slow-query WARNING was logged"
    assert any(getattr(x, "request_id", None) == "req-slow-42" for x in recs)
    assert any("req-slow-42" in x.getMessage() for x in recs)


# ── dashboard cache in Redis ─────────────────────────────────────────────────
class _FakePipeline:
    """Enough of a redis.asyncio pipeline for RateLimitMiddleware (SET NX EX + INCR + TTL)."""
    def __init__(self, r):
        self.r, self.ops = r, []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def set(self, k, v, ex=None, nx=False):
        self.ops.append(("set", k, v, nx))

    def incr(self, k):
        self.ops.append(("incr", k))

    def ttl(self, k):
        self.ops.append(("ttl", k))

    async def execute(self):
        out = []
        for op in self.ops:
            if op[0] == "set":
                if not (op[3] and op[1] in self.r.counters):
                    self.r.counters[op[1]] = int(op[2])
                out.append(True)
            elif op[0] == "incr":
                self.r.counters[op[1]] = self.r.counters.get(op[1], 0) + 1
                out.append(self.r.counters[op[1]])
            else:
                out.append(60)
        return out


class FakeAsyncRedis:
    """Dashboard cache GET/SET (optionally failing) plus a working rate-limit pipeline, so only
    the dashboard cache's own Redis calls fail when broken=True."""
    def __init__(self, broken=False):
        self.store, self.ex, self.broken, self.counters = {}, {}, broken, {}

    def pipeline(self, transaction=True):
        return _FakePipeline(self)

    async def get(self, k):
        if self.broken:
            raise ConnectionError("redis down")
        return self.store.get(k)

    async def set(self, k, v, ex=None, **_):
        if self.broken:
            raise ConnectionError("redis down")
        self.store[k] = v.encode() if isinstance(v, str) else v
        self.ex[k] = ex
        return True


@pytest.mark.asyncio
async def test_dashboard_cache_is_shared_through_redis(client, tenant, monkeypatch):
    fake = FakeAsyncRedis()
    monkeypatch.setattr(settings, "REDIS_URL", "redis://fake:6379/0")
    monkeypatch.setattr(ratelimit, "_aredis", fake)
    reports._dash_cache.clear()

    first = await client.get("/api/reports/dashboard", headers=tenant)
    assert first.status_code == 200
    keys = [k for k in fake.store if k.startswith("dash:")]
    assert len(keys) == 1, fake.store.keys()
    assert fake.ex[keys[0]] == 30
    assert json.loads(fake.store[keys[0]]) == first.json()

    # Another worker wrote this entry: this worker (empty in-process cache) serves it.
    other = first.json()
    other["stats"]["total_orders"] = 987654
    fake.store[keys[0]] = json.dumps(other).encode()
    reports._dash_cache.clear()
    second = await client.get("/api/reports/dashboard", headers=tenant)
    assert second.status_code == 200
    assert second.json()["stats"]["total_orders"] == 987654


@pytest.mark.asyncio
async def test_dashboard_falls_back_in_process_when_redis_fails(client, tenant, monkeypatch):
    monkeypatch.setattr(settings, "REDIS_URL", "redis://fake:6379/0")
    monkeypatch.setattr(ratelimit, "_aredis", FakeAsyncRedis(broken=True))
    reports._dash_cache.clear()
    r = await client.get("/api/reports/dashboard", headers=tenant)
    assert r.status_code == 200
    assert len(reports._dash_cache) == 1                     # cached in-process instead
    assert ratelimit._aredis is False                        # marked down: retried later, not every call
    r2 = await client.get("/api/reports/dashboard", headers=tenant)
    assert r2.status_code == 200 and r2.json() == r.json()


@pytest.mark.asyncio
async def test_dashboard_without_redis_uses_in_process_cache(client, tenant, monkeypatch):
    monkeypatch.setattr(settings, "REDIS_URL", "")
    monkeypatch.setattr(ratelimit, "_aredis", None)
    reports._dash_cache.clear()
    r = await client.get("/api/reports/dashboard", headers=tenant)
    assert r.status_code == 200
    assert len(reports._dash_cache) == 1
