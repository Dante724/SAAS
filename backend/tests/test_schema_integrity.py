"""QA — database schema integrity.

Guards the invariants verified during the build-109 QA pass: every model maps to a real
table, every model column exists in the schema, and there are no orphan foreign keys.
Uses the metadata created by the test harness (create_all), which mirrors the models.
"""
import pytest
from sqlalchemy import inspect

import backend.models as m


@pytest.mark.asyncio
async def test_every_model_has_a_table(db):
    tables = set(await db.run_sync(lambda s: inspect(s.bind).get_table_names()))
    model_tables = {c.__tablename__ for n in dir(m)
                    if hasattr((c := getattr(m, n)), "__tablename__")
                    and hasattr(c, "__table__")}
    missing = model_tables - tables
    assert not missing, f"models without a table: {missing}"


@pytest.mark.asyncio
async def test_model_columns_exist_in_schema(db):
    def _cols(sync, tname):
        return {c["name"] for c in inspect(sync.bind).get_columns(tname)}

    tables = set(await db.run_sync(lambda s: inspect(s.bind).get_table_names()))
    problems = []
    for n in dir(m):
        c = getattr(m, n)
        if not (hasattr(c, "__tablename__") and hasattr(c, "__table__")):
            continue
        if c.__tablename__ not in tables:
            continue
        dbcols = await db.run_sync(_cols, c.__tablename__)
        for col in c.__table__.columns:
            if col.name not in dbcols:
                problems.append(f"{c.__tablename__}.{col.name}")
    assert not problems, f"model columns missing from schema: {problems}"


@pytest.mark.asyncio
async def test_no_orphan_foreign_keys(db):
    def _fks(sync):
        insp = inspect(sync.bind)
        tables = set(insp.get_table_names())
        bad = []
        for t in tables:
            for fk in insp.get_foreign_keys(t):
                if fk["referred_table"] not in tables:
                    bad.append(f"{t} -> {fk['referred_table']}")
        return bad

    orphans = await db.run_sync(_fks)
    assert not orphans, f"orphan foreign keys: {orphans}"


@pytest.mark.asyncio
async def test_phase_cd_tables_present_and_indexed(db):
    """The Phase C/D tables exist and their tenant column is indexed (hot path)."""
    def _check(sync):
        insp = inspect(sync.bind)
        tables = set(insp.get_table_names())
        wanted = {"inbox_threads", "inbox_messages", "email_accounts",
                  "ecommerce_integrations", "ecommerce_order_maps", "approval_requests"}
        assert wanted <= tables, f"missing Phase C/D tables: {wanted - tables}"
        # org_id indexed on the big two
        for t in ("inbox_threads", "inbox_messages"):
            idx_cols = {col for ix in insp.get_indexes(t) for col in ix["column_names"]}
            assert "org_id" in idx_cols, f"{t}.org_id not indexed"
        # Call AI columns + role refund ceiling
        call_cols = {c["name"] for c in insp.get_columns("calls")}
        assert {"transcript", "summary", "sentiment", "ai_processed_at"} <= call_cols
        role_cols = {c["name"] for c in insp.get_columns("roles")}
        assert "refund_ceiling" in role_cols

    await db.run_sync(_check)
