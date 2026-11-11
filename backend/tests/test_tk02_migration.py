"""tk02 (overdue alerts, shared commission, My day indexes) on a migrated SQLite database.

Tasks already overdue when it ships are marked alerted (no burst of alerts for old work), the new
indexes exist, and downgrade removes exactly what it added.
"""
import sqlite3

from backend.tests.test_tk01_migration import _alembic


def test_tk02_upgrade_and_downgrade(tmp_path):
    path = tmp_path / "tk02.db"
    url = f"sqlite+aiosqlite:///{path}"
    _alembic(url, "upgrade", "tk01")
    con = sqlite3.connect(path)
    rows = [  # id, status, due_at, archived_at
        ("late", "todo", "2020-01-01 10:00:00", None),
        ("late-done", "done", "2020-01-01 10:00:00", None),
        ("late-deleted", "todo", "2020-01-01 10:00:00", "2020-01-02 10:00:00"),
        ("future", "todo", "2999-01-01 10:00:00", None),
        ("undated", "in_progress", None, None),
    ]
    for tid, status, due, archived in rows:
        con.execute("INSERT INTO tasks (id, org_id, title, status, priority, source, due_at, archived_at, "
                    "created_at) VALUES (?, 'org', 'x', ?, 0, 'manual', ?, ?, '2026-11-01 10:00:00')",
                    (tid, status, due, archived))
    con.commit()
    con.close()

    _alembic(url, "upgrade", "tk02")
    con = sqlite3.connect(path)
    stamped = {r[0]: r[1] is not None for r in con.execute("SELECT id, overdue_alerted_at FROM tasks")}
    assert stamped == {"late": True, "late-done": False, "late-deleted": False, "future": False,
                       "undated": False}
    idx = {r[1] for r in con.execute("PRAGMA index_list('tasks')")}
    assert {"ix_tasks_org_live_due", "ix_tasks_org_completed", "ix_tasks_overdue_pending",
            "ix_tasks_org_assignee", "ix_tasks_org_creator"} <= idx
    assert "ix_tasks_assignee_ids_gin" not in idx                     # Postgres only
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "task_overdue_alerts" in tables
    org_cols = {r[1] for r in con.execute("PRAGMA table_info('organisations')")}
    assert "task_commission_mode" in org_cols
    con.close()

    _alembic(url, "downgrade", "tk01")
    con = sqlite3.connect(path)
    assert "overdue_alerted_at" not in {r[1] for r in con.execute("PRAGMA table_info('tasks')")}
    assert "task_commission_mode" not in {r[1] for r in con.execute("PRAGMA table_info('organisations')")}
    assert not {"ix_tasks_org_live_due", "ix_tasks_overdue_pending"} & {
        r[1] for r in con.execute("PRAGMA index_list('tasks')")}
    assert con.execute("SELECT count(*) FROM tasks").fetchone()[0] == 5
    assert "task_overdue_alerts" not in {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
