"""144ep — scripts/repair_dev_db.py: moves legacy bookings rows, removes orphan sections,
dry-runs by default, backs up before writing, and is idempotent."""
import importlib.util
import sqlite3
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "repair_dev_db.py"
spec = importlib.util.spec_from_file_location("repair_dev_db", SCRIPT)
repair = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repair)

COLS = "id TEXT PRIMARY KEY, org_id TEXT NOT NULL, scheduled_for TEXT NOT NULL, status TEXT NOT NULL"


def _make(path):
    c = sqlite3.connect(path)
    c.executescript(f"""
        CREATE TABLE appointments ({COLS});
        CREATE TABLE bookings ({COLS});
        INSERT INTO bookings VALUES ('b1','o','2026-10-01 09:00','booked'), ('b2','o','2026-10-02 10:00','done');
        CREATE TABLE checklist_steps (id TEXT PRIMARY KEY);
        CREATE TABLE checklist_sections (id TEXT PRIMARY KEY, step_id TEXT);
        CREATE TABLE checklist_template_items (id TEXT PRIMARY KEY, section_id TEXT);
        INSERT INTO checklist_steps VALUES ('st1');
        INSERT INTO checklist_sections VALUES ('ok','st1'), ('orphan','gone');
        INSERT INTO checklist_template_items VALUES ('i-ok','ok'), ('i-orphan','orphan');
    """)
    c.commit()
    c.close()


def _q(path, sql):
    c = sqlite3.connect(path)
    try:
        return c.execute(sql).fetchall()
    finally:
        c.close()


def test_dry_run_changes_nothing(tmp_path):
    db = tmp_path / "dev.db"
    _make(db)
    before = db.read_bytes()
    repair.main([str(db)])
    assert db.read_bytes() == before
    assert not list(tmp_path.glob("*.backup-*"))


def test_apply_moves_rows_removes_orphans_and_is_idempotent(tmp_path):
    db = tmp_path / "dev.db"
    _make(db)
    repair.main([str(db), "--apply"])

    assert _q(db, "SELECT * FROM appointments ORDER BY id") == [
        ("b1", "o", "2026-10-01 09:00", "booked"), ("b2", "o", "2026-10-02 10:00", "done")]
    assert not _q(db, "SELECT name FROM sqlite_master WHERE name='bookings'")
    assert _q(db, "SELECT id FROM checklist_sections") == [("ok",)]
    assert _q(db, "SELECT id FROM checklist_template_items") == [("i-ok",)]
    backups = list(tmp_path.glob("dev.backup-*.db"))
    assert len(backups) == 1 and _q(backups[0], "SELECT COUNT(*) FROM bookings") == [(2,)]

    after = db.read_bytes()
    repair.main([str(db), "--apply"])            # second run: nothing to do, no new backup
    assert db.read_bytes() == after
    assert len(list(tmp_path.glob("dev.backup-*.db"))) == 1


def test_refuses_on_id_clash(tmp_path):
    db = tmp_path / "dev.db"
    _make(db)
    c = sqlite3.connect(db)
    c.execute("INSERT INTO appointments VALUES ('b1','o','2026-01-01','booked')")
    c.commit()
    c.close()
    with pytest.raises(SystemExit, match="already exist"):
        repair.main([str(db), "--apply"])
    assert _q(db, "SELECT COUNT(*) FROM bookings") == [(2,)]


def test_refuses_in_production(tmp_path, monkeypatch):
    db = tmp_path / "dev.db"
    _make(db)
    monkeypatch.setenv("ENVIRONMENT", "production")
    with pytest.raises(SystemExit, match="production"):
        repair.main([str(db), "--apply"])
