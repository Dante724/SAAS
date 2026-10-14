"""Repair a LOCAL dev SQLite database built by create_all (never by migrations).

Fixes two leftovers that migrations would have handled on a migrated DB:
  1. A legacy `bookings` table (pre-rename) whose rows are invisible to the app, which reads
     `appointments`. Rows are copied across by id, verified, then the legacy table is dropped.
  2. checklist_sections whose step_id points at a deleted step (created by the old
     _write_structure while SQLite FK enforcement was off; fixed in 144ep). Unreachable from any
     template, so they and any template items under them are deleted.

Safe by default:
  * DRY RUN unless --apply. Prints exactly what it would do.
  * SQLite only; refuses when ENVIRONMENT=production.
  * --apply first writes a consistent backup next to the DB (sqlite backup API), then does all
    changes in ONE transaction and verifies before committing; any mismatch rolls back.
  * Idempotent: a second run finds nothing to do.

Usage:  python scripts/repair_dev_db.py [path/to/repairos.db] [--apply]
"""
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ORPHAN_SECTIONS = ("SELECT id FROM checklist_sections s WHERE s.step_id IS NOT NULL AND NOT EXISTS "
                   "(SELECT 1 FROM checklist_steps p WHERE p.id = s.step_id)")


def _tables(c):
    return {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _cols(c, t):
    return [r[1] for r in c.execute(f"PRAGMA table_info({t})")]


def plan(c):
    """Return (bookings_to_move, orphan_section_ids, problems). Read-only."""
    problems, move = [], []
    tables = _tables(c)
    if "bookings" in tables:
        if "appointments" not in tables:
            problems.append("legacy `bookings` exists but `appointments` does not — run migrations instead")
        else:
            missing = set(_cols(c, "bookings")) - set(_cols(c, "appointments"))
            if missing:
                problems.append(f"bookings has columns appointments lacks: {sorted(missing)}")
            clash = c.execute("SELECT COUNT(*) FROM bookings b JOIN appointments a ON a.id = b.id").fetchone()[0]
            if clash:
                problems.append(f"{clash} booking id(s) already exist in appointments — resolve by hand")
            move = [r[0] for r in c.execute("SELECT id FROM bookings ORDER BY id")]
    orphans = [r[0] for r in c.execute(ORPHAN_SECTIONS)] if "checklist_sections" in tables else []
    return move, orphans, problems


def apply(c, move, orphans):
    c.execute("BEGIN IMMEDIATE")
    try:
        if "bookings" in _tables(c):
            cols = ",".join(_cols(c, "bookings"))
            c.execute(f"INSERT INTO appointments ({cols}) SELECT {cols} FROM bookings")
            # verify every row arrived byte-identical before dropping the source
            diff = c.execute(f"SELECT COUNT(*) FROM (SELECT {cols} FROM bookings EXCEPT "
                             f"SELECT {cols} FROM appointments)").fetchone()[0]
            if diff:
                raise RuntimeError(f"{diff} booking row(s) did not copy identically")
            c.execute("DROP TABLE bookings")
        if orphans:
            q = ",".join("?" * len(orphans))
            c.execute(f"DELETE FROM checklist_template_items WHERE section_id IN ({q})", orphans)
            c.execute(f"DELETE FROM checklist_sections WHERE id IN ({q})", orphans)
        left = c.execute(ORPHAN_SECTIONS).fetchall()
        if left:
            raise RuntimeError(f"{len(left)} orphan section(s) remain")
        c.execute("COMMIT")
    except Exception:
        c.execute("ROLLBACK")
        raise


def main(argv):
    args = [a for a in argv if not a.startswith("--")]
    do_apply = "--apply" in argv
    if os.environ.get("ENVIRONMENT", "").lower() == "production":
        sys.exit("Refusing: ENVIRONMENT=production. This script is for local dev databases only.")
    db = Path(args[0] if args else "repairos.db")
    if not db.is_file():
        sys.exit(f"No SQLite database at {db}")

    c = sqlite3.connect(db, isolation_level=None)
    try:
        move, orphans, problems = plan(c)
        print(f"Database: {db}")
        print(f"  legacy bookings rows to move into appointments: {len(move)}")
        print(f"  orphan checklist_sections to delete:            {len(orphans)}")
        if problems:
            sys.exit("Cannot repair automatically:\n  " + "\n  ".join(problems))
        if not move and not orphans and "bookings" not in _tables(c):
            print("Nothing to do.")
            return
        if not do_apply:
            print("Dry run — nothing changed. Re-run with --apply.")
            return
        backup = db.with_name(f"{db.stem}.backup-{datetime.now():%Y%m%d-%H%M%S}{db.suffix}")
        b = sqlite3.connect(backup)
        try:
            c.backup(b)
        finally:
            b.close()
        print(f"  backup written: {backup}")
        apply(c, move, orphans)
        print("Done. Verified and committed.")
    finally:
        c.close()


if __name__ == "__main__":
    main(sys.argv[1:])
