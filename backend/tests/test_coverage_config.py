"""The coverage ratchet must see async DB code.

SQLAlchemy's asyncio layer runs every ORM call inside a greenlet. Without
``concurrency = ["greenlet", ...]`` coverage.py loses the trace the moment a
handler awaits the database, so the floor in ``[tool.coverage.report]`` is
enforced on an under-count (hardening council Q1: cash.py 32% vs 48% on the same tests).
"""
from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parents[2]


def test_coverage_traces_sqlalchemy_greenlets():
    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text())
    run = cfg["tool"]["coverage"]["run"]
    assert "greenlet" in run.get("concurrency", []), \
        "[tool.coverage.run] concurrency must include 'greenlet' or async DB paths are not measured"
    assert "thread" in run["concurrency"], "the app also runs work in threads (run_in_threadpool)"
