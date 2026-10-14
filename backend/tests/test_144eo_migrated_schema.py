"""144eo — tests that run against a database built by the MIGRATIONS (like production), not by
create_all (like the rest of the suite). The whole suite builds its schema from the models, so any
place where the migrations disagree with the models was invisible to it — that's how a fired
notification rule became undeletable on migrated databases. These tests close that blind spot.

1. Hard drift gate: migrated schema must match the models, except an explicit, shrinking allowlist
   of known differences. Any NEW mismatch (missing table/column/index, length, FK, ondelete, or a
   nullable change in the dangerous direction) fails CI.
2. Behaviour on the migrated schema: deleting a rule that has fired keeps its logs.
3. API validation for document template page_size.
"""
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine

from backend.db.database import Base
from backend.models import all_models  # noqa: F401 — registers every table

REPO = Path(__file__).resolve().parents[2]

# ── Known, reviewed differences (tracked debt). The gate fails on anything NOT listed here, and
# also fails if a listed item is no longer a difference — so this list can only shrink. ──
# Foreign keys the models declare but no migration ever created (production does not enforce
# them). Adding them needs a check for existing orphan rows first: scripts/check_fk_orphans.py.
KNOWN_MISSING_FKS = {
    ("bundles", "category_id"), ("commission_rules", "service_id"), ("coupons", "client_id"),
    ("devices", "org_id"), ("directory_items", "location_id"), ("orders", "manager_id"),
    ("parts", "default_supplier_id"), ("purchase_orders", "supplier_invoice_blob_id"),
    ("services", "category_id"), ("time_entries", "task_id"), ("transactions", "voided_by_id"),
}
# Equivalent types: unbounded VARCHAR vs TEXT store the same thing on SQLite and Postgres.
KNOWN_TYPE_DIFFS = {("clients", "billing_address")}


@pytest.fixture(scope="module")
def migrated_db(tmp_path_factory):
    """A fresh SQLite database built ONLY by `alembic upgrade head`."""
    path = tmp_path_factory.mktemp("migrated") / "migrated.db"
    env = dict(os.environ, ENVIRONMENT="test", SEED_DEMO="false",
               DATABASE_URL=f"sqlite+aiosqlite:///{path}")
    env.pop("TEST_DATABASE_URL", None)
    r = subprocess.run([sys.executable, "-m", "alembic", "-c", "backend/alembic.ini", "upgrade", "head"],
                       cwd=REPO, env=env, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, f"migrations failed:\n{r.stderr[-3000:]}"
    return path


def _drift(db_path):
    eng = create_engine(f"sqlite:///{db_path}")
    try:
        with eng.connect() as conn:
            diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    finally:
        eng.dispose()
    flat = []
    for d in diff:
        flat.extend(d if isinstance(d, list) else [d])
    return flat


def test_migrated_schema_matches_models(migrated_db):
    unexpected, seen_fks, seen_types = [], set(), set()
    for op in _drift(migrated_db):
        kind = op[0]
        if kind == "modify_nullable":
            _, _, table, col, _, db_nullable, model_nullable = op
            if db_nullable is True and model_nullable is False:
                continue  # DB looser than the model: the ORM always sends a value, nothing can fail
            unexpected.append(f"{table}.{col}: NOT NULL in DB but nullable in model (inserts can fail)")
        elif kind == "add_fk":
            fk = op[1]
            key = (fk.table.name, fk.columns[0].name)
            if key in KNOWN_MISSING_FKS:
                seen_fks.add(key)
                continue
            unexpected.append(f"FK in model but not in migrations: {key[0]}.{key[1]}")
        elif kind == "modify_type":
            key = (op[2], op[3])
            if key in KNOWN_TYPE_DIFFS:
                seen_types.add(key)
                continue
            unexpected.append(f"type differs: {key[0]}.{key[1]} db={op[5]} model={op[6]}")
        else:
            unexpected.append(f"{kind}: {op[1:]}"[:300])
    stale = sorted(KNOWN_MISSING_FKS - seen_fks) + sorted(KNOWN_TYPE_DIFFS - seen_types)
    assert not unexpected, (
        "The migrations no longer build the schema the models describe — add a migration:\n  "
        + "\n  ".join(unexpected))
    assert not stale, f"Fixed — remove from the allowlist in this file: {stale}"


def test_fired_rule_delete_keeps_logs_on_migrated_schema(migrated_db):
    c = sqlite3.connect(migrated_db)
    try:
        c.execute("PRAGMA foreign_keys=ON")  # the app enforces FKs
        # minimal parents for the NOT NULL org FK: insert with FKs off, then turn them back on
        c.execute("PRAGMA foreign_keys=OFF")
        cols = lambda t: {r[1]: r for r in c.execute(f"PRAGMA table_info({t})")}  # noqa: E731
        def ins(t, **kv):
            for name, r in cols(t).items():
                if r[3] and r[4] is None and name not in kv:   # NOT NULL without default
                    kv[name] = 0 if any(x in (r[2] or "").upper() for x in ("INT", "BOOL", "NUM", "FLOAT")) else "x"
            c.execute(f"INSERT INTO {t} ({','.join(kv)}) VALUES ({','.join('?' * len(kv))})", list(kv.values()))
        ins("notification_rules", id="rule-fired")
        for i in range(3):
            ins("notification_logs", id=f"log-{i}", rule_id="rule-fired")
        c.commit()
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("DELETE FROM notification_rules WHERE id='rule-fired'")  # used to be rejected
        c.commit()
        assert c.execute("SELECT COUNT(*) FROM notification_logs").fetchone()[0] == 3     # history kept
        assert c.execute("SELECT COUNT(*) FROM notification_logs WHERE rule_id IS NULL").fetchone()[0] == 3
    finally:
        c.close()


async def test_template_page_size_validated(client, tenant):
    base = {"name": "T", "doc_type": "invoice", "body_html": "<p>x</p>"}
    bad = await client.post("/api/documents/templates", json={**base, "page_size": "a4-landscape-x"}, headers=tenant)
    assert bad.status_code == 422, bad.text
    ok = await client.post("/api/documents/templates", json={**base, "page_size": "receipt58"}, headers=tenant)
    assert ok.status_code == 201, ok.text
    tid = ok.json()["id"]
    blank = await client.put(f"/api/documents/templates/{tid}", json={**base, "page_size": ""}, headers=tenant)
    assert blank.status_code == 200, blank.text          # empty keeps the historic fallback
    bad_put = await client.put(f"/api/documents/templates/{tid}", json={**base, "page_size": "tabloid"}, headers=tenant)
    assert bad_put.status_code == 422, bad_put.text
