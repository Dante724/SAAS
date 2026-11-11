"""Restore drill (scripts/restore_drill.py): a dump restored into a scratch database must match.

The council: "the backup restore drill has never been run". The drill dumps a database with
pg_dump, restores it into a scratch database and checks the copy: same migration version,
same row count in every table, and no new integrity mismatches. These tests run it against the
suite's own Postgres database and prove it fails when the restore is incomplete.
"""
import os
import shutil
import subprocess

import pytest

_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL", "")


def _pg_client_16() -> bool:
    exe = shutil.which("pg_dump")
    if not exe:
        return False
    out = subprocess.run([exe, "--version"], capture_output=True, text=True).stdout
    try:
        return int(out.split()[-1].split(".")[0]) >= 16
    except (IndexError, ValueError):
        return False


_pg = pytest.mark.skipif(not (_URL.startswith("postgresql") and _pg_client_16()),
                         reason="needs Postgres and the Postgres 16 client tools (the CI Postgres job installs them)")


def _drill():
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[2] / "scripts" / "restore_drill.py"
    spec = importlib.util.spec_from_file_location("restore_drill", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def _some_money(client, h):
    """A client, a job and a till payment: rows in the ledgers the drill re-checks."""
    loc = (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]
    till = (await client.post("/api/cash/accounts", json={"name": "Till", "payment_option": "cash"},
                              headers=h)).json()["id"]
    cid = (await client.post("/api/clients", json={"name": "Drill"}, headers=h)).json()["id"]
    r = await client.post("/api/orders", headers=h, json={
        "client_id": cid, "location_id": loc,
        "lines": [{"line_type": "labour", "description": "Fix", "qty": 1, "unit_price": 80}]})
    assert r.status_code == 201, r.text
    r = await client.post(f"/api/cash/orders/{r.json()['id']}/pay", headers=h,
                          json={"method": "cash", "account_id": till, "amount": 80})
    assert r.status_code == 200, r.text


@_pg
@pytest.mark.asyncio
async def test_drill_restores_an_identical_copy(client, tenant):
    await _some_money(client, tenant)
    rd = _drill()
    rep = await rd.drill(_URL, check_head=False)     # the suite builds its schema with create_all
    assert rep["ok"], rep["problems"]
    assert rep["rows"] > 0 and rep["tables"] > 50
    assert set(rep["timings"]) >= {"dump_s", "restore_s", "verify_s", "total_s"}
    assert rep["scratch_db"] is None                  # dropped after the drill


@_pg
@pytest.mark.asyncio
async def test_drill_fails_when_the_restore_is_incomplete(client, tenant, monkeypatch):
    """A restore that brings back only one table must be reported, table by table."""
    await _some_money(client, tenant)
    rd = _drill()
    real = rd._run

    def partial(cmd):
        if os.path.basename(cmd[0]) == "pg_restore":
            cmd = [cmd[0], "--table=accounts", *cmd[1:]]
        return real(cmd)
    monkeypatch.setattr(rd, "_run", partial)
    rep = await rd.drill(_URL, check_head=False)
    assert not rep["ok"]
    assert any(p.startswith("table clients:") for p in rep["problems"]), rep["problems"]


def test_compare_names_every_kind_of_difference():
    rd = _drill()
    src = {"alembic": ["a1"], "rows": {"clients": 3, "orders": 2}, "mismatches": {}}
    dst = {"alembic": ["a0"], "rows": {"clients": 3, "orders": 1}, "mismatches": {"account": 1}}
    problems = rd.compare(src, dst, heads=["a1"])
    assert any("alembic version differs" in p for p in problems)
    assert any("migration head" in p for p in problems)
    assert any(p.startswith("table orders: 2") for p in problems)
    assert any("integrity check 'account'" in p for p in problems)
    assert rd.compare(src, dict(src), heads=["a1"]) == []


def test_drill_refuses_a_non_postgres_url(capsys):
    assert _drill().main(["--source-url", "sqlite+aiosqlite:///x.db"]) == 2
