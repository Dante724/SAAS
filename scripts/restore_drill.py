#!/usr/bin/env python3
"""Backup restore drill: dump a Tekly database, restore it into a scratch database, prove it.

    python scripts/restore_drill.py                         # uses $DATABASE_URL
    python scripts/restore_drill.py --source-url postgresql+asyncpg://u:p@host/db --keep
    python scripts/restore_drill.py --dump repairos-2026-11-05.dump   # check an existing nightly dump

Steps (each timed; the total is your measured recovery time for a database this size):
  1. pg_dump --format=custom of the source (skipped with --dump);
  2. create an empty scratch database next to it and pg_restore --no-owner into it;
  3. verify the restored copy:
     - alembic_version matches the source, and the repo's migration head;
     - every table in the public schema has the same row count as the source;
     - the integrity sweep (backend/services/reconcile.py) finds no MORE mismatches than on the
       source — a restore must not introduce drift;
  4. drop the scratch database (unless --keep).

Exit code 0 = the drill passed; 1 = a check failed (the report says which); 2 = setup error
(no pg_dump/pg_restore 16+, can't connect, can't create the scratch database).
Needs: the Postgres 16 client tools on PATH, and a role allowed to CREATE DATABASE.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


_SWEEP_TABLES = {"accounts", "transactions", "clients", "client_ledger_entries", "invoices", "payments",
                 "stock_levels", "stock_movements", "locations"}


class SetupError(Exception):
    pass


def _libpq_url(url: str) -> str:
    """postgresql+asyncpg://… → postgresql://… (what pg_dump / pg_restore understand)."""
    return re.sub(r"^postgresql\+\w+://", "postgresql://", url)


def _with_db(url: str, name: str) -> str:
    from sqlalchemy.engine import make_url
    return make_url(url).set(database=name).render_as_string(hide_password=False)


def _tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise SetupError(f"{name} not found on PATH (install the Postgres 16 client tools)")
    out = subprocess.run([path, "--version"], capture_output=True, text=True).stdout
    m = re.search(r"(\d+)\.", out)
    if not m or int(m.group(1)) < 16:
        raise SetupError(f"{name} is {out.strip() or 'unknown'}; the server is Postgres 16, use client 16+")
    return path


def _run(cmd: list[str]) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        # Never echo the command line: it carries the database URL (and its password).
        raise SetupError(f"{Path(cmd[0]).name} failed (exit {r.returncode}): {r.stderr.strip()[:500]}")


async def _snapshot(url: str) -> dict:
    """alembic version, per-table row counts and integrity mismatch counts of one database."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.pool import NullPool

    from backend.services.reconcile import find_mismatches

    eng = create_async_engine(url, poolclass=NullPool)
    try:
        async with eng.connect() as conn:
            tables = (await conn.execute(text(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"))).scalars().all()
            counts = {}
            for t in tables:
                counts[t] = (await conn.execute(text(f'SELECT count(*) FROM "{t}"'))).scalar()
            version = None
            if "alembic_version" in tables:
                version = sorted((await conn.execute(text("SELECT version_num FROM alembic_version"))).scalars().all())
        async with AsyncSession(eng) as db:
            mismatches = {}
            # The sweep needs every ledger table; a restore missing one already fails on row counts.
            if _SWEEP_TABLES <= set(tables):
                for m in await find_mismatches(db):
                    mismatches[m.check] = mismatches.get(m.check, 0) + 1
        return {"alembic": version, "rows": counts, "mismatches": mismatches}
    finally:
        await eng.dispose()


def _repo_heads() -> list[str]:
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    cfg = Config(str(REPO / "backend" / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO / "backend" / "migrations"))
    return sorted(ScriptDirectory.from_config(cfg).get_heads())


async def _admin(url: str, sql: str) -> None:
    import asyncpg
    conn = await asyncpg.connect(_libpq_url(_with_db(url, "postgres")))
    try:
        await conn.execute(sql)
    finally:
        await conn.close()


def compare(src: dict, dst: dict, heads: list[str] | None) -> list[str]:
    """Human-readable problems with the restored copy (empty = it matches)."""
    problems = []
    if src["alembic"] != dst["alembic"]:
        problems.append(f"alembic version differs: source {src['alembic']} vs restored {dst['alembic']}")
    if heads is not None and dst["alembic"] is not None and dst["alembic"] != heads:
        problems.append(f"restored schema is at {dst['alembic']}, the code's migration head is {heads} "
                        "(run `alembic upgrade head` after restoring)")
    for t in sorted(set(src["rows"]) | set(dst["rows"])):
        a, b = src["rows"].get(t), dst["rows"].get(t)
        if a != b:
            problems.append(f"table {t}: {a} row(s) in the source, {b} restored")
    for check, n in dst["mismatches"].items():
        if n > src["mismatches"].get(check, 0):
            problems.append(f"integrity check '{check}': {n} mismatch(es) after restore, "
                            f"{src['mismatches'].get(check, 0)} before")
    return problems


async def drill(source_url: str, *, dump: str | None = None, keep: bool = False,
                check_head: bool = True) -> dict:
    pg_dump, pg_restore = _tool("pg_dump"), _tool("pg_restore")
    timings: dict[str, float] = {}
    scratch = f"tekly_restore_drill_{uuid.uuid4().hex[:8]}"
    scratch_url = _with_db(source_url, scratch)
    t0 = time.monotonic()
    with tempfile.TemporaryDirectory() as tmp:
        path = dump
        if not path:
            path = os.path.join(tmp, "drill.dump")
            s = time.monotonic()
            _run([pg_dump, "--format=custom", "--no-owner", f"--file={path}", f"--dbname={_libpq_url(source_url)}"])
            timings["dump_s"] = round(time.monotonic() - s, 2)
        await _admin(source_url, f'CREATE DATABASE "{scratch}"')
        try:
            s = time.monotonic()
            _run([pg_restore, "--no-owner", "--exit-on-error", f"--dbname={_libpq_url(scratch_url)}", path])
            timings["restore_s"] = round(time.monotonic() - s, 2)
            s = time.monotonic()
            src = await _snapshot(source_url)
            dst = await _snapshot(scratch_url)
            timings["verify_s"] = round(time.monotonic() - s, 2)
        finally:
            if not keep:
                await _admin(source_url, f'DROP DATABASE IF EXISTS "{scratch}" WITH (FORCE)')
        dump_bytes = os.path.getsize(path)
    timings["total_s"] = round(time.monotonic() - t0, 2)
    problems = compare(src, dst, _repo_heads() if check_head else None)
    return {"ok": not problems, "problems": problems, "timings": timings, "dump_bytes": dump_bytes,
            "tables": len(dst["rows"]), "rows": sum(v or 0 for v in dst["rows"].values()),
            "alembic": dst["alembic"], "scratch_db": scratch if keep else None}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--source-url", default=os.environ.get("DATABASE_URL", ""))
    ap.add_argument("--dump", help="check this existing pg_dump --format=custom file instead of dumping")
    ap.add_argument("--keep", action="store_true", help="keep the scratch database for inspection")
    ap.add_argument("--no-head-check", action="store_true",
                    help="don't require the restored schema to be at the code's migration head")
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    a = ap.parse_args(argv)
    if not a.source_url.startswith("postgresql"):
        print("restore drill: --source-url / DATABASE_URL must be a Postgres URL", file=sys.stderr)
        return 2
    try:
        rep = asyncio.run(drill(a.source_url, dump=a.dump, keep=a.keep, check_head=not a.no_head_check))
    except SetupError as e:
        print(f"restore drill: setup error: {e}", file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps(rep, indent=2))
    else:
        print(f"restore drill: {'PASS' if rep['ok'] else 'FAIL'} — {rep['tables']} tables, {rep['rows']} rows, "
              f"dump {rep['dump_bytes'] / 1e6:.1f} MB, schema {rep['alembic']}")
        print("  timings: " + ", ".join(f"{k}={v}s" for k, v in rep["timings"].items()))
        for p in rep["problems"]:
            print(f"  ✗ {p}")
        if rep["scratch_db"]:
            print(f"  kept scratch database {rep['scratch_db']}")
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
