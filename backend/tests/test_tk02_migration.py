"""tk02 (overdue alerts, shared commission, My day indexes, due times to UTC) on a migrated SQLite
database.

- Due times people typed (source "manual") move from shop wall time to UTC; a bare date stored at
  00:00 becomes the end of that shop day; automation rows are untouched.
- Open tasks already overdue are stamped as alerted, and their current assignees are recorded as
  told (so adding someone later alerts only the newcomer).
- The new indexes and table exist; downgrade removes them and puts manual due times back on the
  wall clock.
"""
import sqlite3

from backend.tests.test_tk01_migration import _alembic


def _insert(con, table: str, values: dict) -> None:
    """Insert with every other NOT NULL column (no default) filled with a neutral value."""
    for _cid, name, typ, notnull, default, pk in con.execute(f"PRAGMA table_info('{table}')"):
        if notnull and default is None and not pk and name not in values:
            t = (typ or "").upper()
            values[name] = 0 if any(k in t for k in ("INT", "BOOL", "NUM", "FLOAT", "REAL")) else ""
    cols = ", ".join(values)
    con.execute(f"INSERT INTO {table} ({cols}) VALUES ({', '.join('?' * len(values))})", list(values.values()))


ROWS = [  # id, status, source, due_at, archived_at, assigned_to_id, assignee_ids
    ("late", "todo", "manual", "2020-01-01 10:00:00", None, "e1", '["e1", "e2"]'),
    ("late-done", "done", "manual", "2020-01-01 10:00:00", None, "e1", '["e1"]'),
    ("late-deleted", "todo", "manual", "2020-01-01 10:00:00", "2020-01-02 10:00:00", None, "[]"),
    ("future", "todo", "manual", "2999-01-01 18:00:00", None, None, "[]"),
    ("bare-date", "todo", "manual", "2999-03-10 00:00:00", None, None, "[]"),
    ("auto", "todo", "automation", "2999-01-01 18:00:00", None, None, "[]"),
    ("undated", "in_progress", "manual", None, None, None, None),
]


def test_tk02_upgrade_and_downgrade(tmp_path):
    path = tmp_path / "tk02.db"
    url = f"sqlite+aiosqlite:///{path}"
    _alembic(url, "upgrade", "nob01")
    con = sqlite3.connect(path)
    _insert(con, "organisations", {"id": "org", "name": "Shop", "timezone": "Asia/Kolkata"})
    for emp in ("e1", "e2"):
        _insert(con, "employees", {"id": emp, "org_id": "org", "name": emp, "email": f"{emp}@x.dev"})
    for tid, status, source, due, archived, primary, extra in ROWS:
        con.execute("INSERT INTO tasks (id, org_id, title, status, priority, source, due_at, archived_at, "
                    "assigned_to_id, assignee_ids, created_at) "
                    "VALUES (?, 'org', 'x', ?, 0, ?, ?, ?, ?, ?, '2026-11-01 10:00:00')",
                    (tid, status, source, due, archived, primary, extra))
    con.commit()
    con.close()

    _alembic(url, "upgrade", "tk02")
    con = sqlite3.connect(path)
    due = {r[0]: r[1] for r in con.execute("SELECT id, due_at FROM tasks")}
    assert due["future"].startswith("2999-01-01 12:30:00")          # 18:00 IST -> 12:30 UTC
    assert due["bare-date"].startswith("2999-03-10 18:29:59")       # end of that IST day
    assert due["auto"].startswith("2999-01-01 18:00:00")            # already UTC: untouched
    assert due["late"].startswith("2020-01-01 04:30:00")
    assert due["undated"] is None
    stamped = {r[0]: r[1] is not None for r in con.execute("SELECT id, overdue_alerted_at FROM tasks")}
    assert stamped == {"late": True, "late-done": False, "late-deleted": False, "future": False,
                       "bare-date": False, "auto": False, "undated": False}
    told = sorted(r[0] for r in con.execute("SELECT employee_id FROM task_overdue_alerts WHERE task_id = 'late'"))
    assert told == ["e1", "e2"]
    idx = {r[1] for r in con.execute("PRAGMA index_list('tasks')")}
    assert {"ix_tasks_org_live_due", "ix_tasks_org_completed", "ix_tasks_overdue_pending",
            "ix_tasks_org_assignee", "ix_tasks_org_creator"} <= idx
    assert "ix_tasks_assignee_ids_gin" not in idx                     # Postgres only
    org_cols = {r[1] for r in con.execute("PRAGMA table_info('organisations')")}
    assert "task_commission_mode" in org_cols
    con.close()

    _alembic(url, "downgrade", "nob01")
    con = sqlite3.connect(path)
    assert "overdue_alerted_at" not in {r[1] for r in con.execute("PRAGMA table_info('tasks')")}
    assert "task_commission_mode" not in {r[1] for r in con.execute("PRAGMA table_info('organisations')")}
    assert not {"ix_tasks_org_live_due", "ix_tasks_overdue_pending"} & {
        r[1] for r in con.execute("PRAGMA index_list('tasks')")}
    assert "task_overdue_alerts" not in {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    due = {r[0]: r[1] for r in con.execute("SELECT id, due_at FROM tasks")}
    assert due["future"].startswith("2999-01-01 18:00:00")          # back on the wall clock
    assert due["bare-date"].startswith("2999-03-10 23:59:59")
    assert due["auto"].startswith("2999-01-01 18:00:00")
    assert con.execute("SELECT count(*) FROM tasks").fetchone()[0] == len(ROWS)
    con.close()
