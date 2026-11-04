"""Liveness never depends on the database; readiness does.

Regression: /api/health ran SELECT 1 and returned 503 when the DB was unreachable, and the
Dockerfile HEALTHCHECK probed it, so a short database blip marked every container unhealthy and
the orchestrator restarted all of them at once (which cannot fix a database outage). Liveness
(/api/health, /health) now only says the process is serving; /api/readyz keeps the DB check for
load balancers that should stop routing traffic to an instance that can't reach the DB.
"""
import re
from pathlib import Path

import pytest

from backend.db.database import get_db
from main import app

REPO = Path(__file__).resolve().parents[2]


class _DeadSession:
    async def execute(self, *_a, **_k):
        raise ConnectionRefusedError("database is down")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False


@pytest.fixture
def db_down(monkeypatch):
    """Every way the app reaches the database fails."""
    import backend.db.database as database

    monkeypatch.setattr(database, "AsyncSessionLocal", lambda *a, **k: _DeadSession())

    async def _dead_get_db():
        yield _DeadSession()
    prev = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = _dead_get_db
    yield
    app.dependency_overrides[get_db] = prev


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/health", "/health"])
async def test_liveness_ok_while_database_is_down(client, db_down, path):
    r = await client.get(path)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_readyz_reports_database_down(client, db_down):
    r = await client.get("/api/readyz")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "unready" and body["database"] == "down"
    assert "refused" not in r.text.lower()        # driver error text stays in the log


@pytest.mark.asyncio
async def test_readyz_ok_with_database(client):
    r = await client.get("/api/readyz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ready" and body["db"] == "ok" and body["database"] == "up"


def test_docker_healthcheck_probes_liveness_not_readiness():
    df = (REPO / "Dockerfile").read_text()
    hc = re.search(r"HEALTHCHECK[^\n]*\\\n([^\n]*)", df).group(1)
    assert "/api/health" in hc and "readyz" not in hc
