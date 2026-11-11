"""tk01 (tasks foundations) on a migrated SQLite database: the data fixes and a clean downgrade.

Statuses outside todo|in_progress|done|cancelled become "todo", text priorities become 0-3 integers,
inquiry tasks get their source/source_ref, and the partial unique index exists. Downgrade puts
priority back to text and drops the new columns/table.
"""
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _alembic(url: str, *args: str) -> None:
    env = dict(os.environ, ENVIRONMENT="test", SEED_DEMO="false", DATABASE_URL=url)
    env.pop("TEST_DATABASE_URL", None)
    r = subprocess.run([sys.executable, "-m", "alembic", "-c", "backend/alembic.ini", *args],
                       cwd=REPO, env=env, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, f"alembic {args} failed:\n{r.stderr[-3000:]}"


ROWS = [  # id, status, priority, lead_id
    ("t1", "open", "normal", None),
    ("t2", "todo", "high", None),
    ("t3", "in_progress", "urgent", None),
    ("t4", "weird", "2", None),
    ("t5", "done", "banana", None),
    ("t6", "todo", "7", "lead-1"),
    ("t7", "done", "low", "lead-1"),
]


def test_tk01_converts_data_and_downgrades(tmp_path):
    path = tmp_path / "tk01.db"
    url = f"sqlite+aiosqlite:///{path}"
    _alembic(url, "upgrade", "nn01")
    con = sqlite3.connect(path)   # foreign keys are off in a plain sqlite3 connection
    for tid, status, prio, lead in ROWS:
        con.execute("INSERT INTO tasks (id, org_id, title, status, priority, lead_id, created_at) "
                    "VALUES (?, 'org', 'x', ?, ?, ?, '2026-11-01 10:00:00')", (tid, status, prio, lead))
    con.commit()
    con.close()

    _alembic(url, "upgrade", "tk01")
    con = sqlite3.connect(path)
    got = {r[0]: r[1:] for r in con.execute(
        "SELECT id, status, priority, typeof(priority), source, source_ref, updated_at FROM tasks")}
    assert got["t1"][:3] == ("todo", 0, "integer")
    assert got["t2"][:2] == ("todo", 1)
    assert got["t3"][:2] == ("in_progress", 2)
    assert got["t4"][:2] == ("todo", 2)
    assert got["t5"][:2] == ("done", 0)
    assert got["t6"][:2] == ("todo", 3)
    assert got["t6"][3:5] == ("inquiry", "lead:lead-1")        # the open inquiry task gets the ref
    assert got["t7"][3:5] == ("inquiry", None)                 # closed ones don't
    assert got["t2"][3] == "manual" and got["t2"][5] is not None
    idx = {r[1]: r[2] for r in con.execute("PRAGMA index_list('tasks')")}
    assert idx.get("uq_tasks_open_source_ref") == 1
    cols = {r[1] for r in con.execute("PRAGMA table_info('notification_logs')")}
    assert "expect_status" in cols
    assert con.execute("SELECT count(*) FROM sqlite_master WHERE name = 'automation_fires'").fetchone()[0] == 1
    con.close()

    _alembic(url, "downgrade", "nn01")
    con = sqlite3.connect(path)
    cols = {r[1]: r[2] for r in con.execute("PRAGMA table_info('tasks')")}
    assert "source_ref" not in cols and "archived_at" not in cols
    assert cols["priority"].upper().startswith("VARCHAR")
    assert dict(con.execute("SELECT id, priority FROM tasks"))["t3"] == "2"
    assert con.execute("SELECT count(*) FROM sqlite_master WHERE name = 'automation_fires'").fetchone()[0] == 0
    con.close()
