"""Production runs Postgres; the main suite runs on SQLite (fast, lenient). These checks need
no Postgres server, so they guard prod-dialect compatibility on EVERY push:

1. Every table and index compiles to valid Postgres DDL — catches SQLite-only types or default
   expressions that would only blow up at deploy time.
2. Every partial index declares its WHERE for BOTH dialects. A partial unique index with only
   `sqlite_where` silently becomes a FULL unique index on Postgres — stricter uniqueness in prod
   than in tests (rows that pass in CI get rejected in production).

The full behavioural run against a real Postgres lives in the `backend-postgres` CI job.
"""
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from backend.db.database import Base
from backend.models import all_models  # noqa: F401 — registers every table on Base.metadata

PG = postgresql.dialect()
TABLES = Base.metadata.sorted_tables


def test_schema_is_not_empty():
    # guard against the import above silently registering nothing (would make the checks vacuous)
    assert len(TABLES) > 100


def test_all_tables_and_indexes_compile_for_postgres():
    # One test over all tables (not parametrized): the conftest rebuilds the schema around every
    # test, so 150+ parametrized cases would cost ~1 min of CI for no extra signal. Failures are
    # collected so a single run reports every offending table/index at once.
    errors = []
    for t in TABLES:
        try:
            CreateTable(t).compile(dialect=PG)
        except Exception as e:  # noqa: BLE001 — report every failure, don't stop at the first
            errors.append(f"TABLE {t.name}: {type(e).__name__}: {e}")
        for ix in t.indexes:
            try:
                CreateIndex(ix).compile(dialect=PG)
            except Exception as e:  # noqa: BLE001
                errors.append(f"INDEX {t.name}.{ix.name}: {type(e).__name__}: {e}")
    assert not errors, "Postgres DDL compile failures:\n" + "\n".join(errors)


def test_partial_indexes_declare_where_for_both_dialects():
    mismatched = []
    for t in TABLES:
        for ix in t.indexes:
            has_sqlite = ix.dialect_options["sqlite"].get("where") is not None
            has_pg = ix.dialect_options["postgresql"].get("where") is not None
            if has_sqlite != has_pg:
                mismatched.append(f"{t.name}.{ix.name} (sqlite_where={has_sqlite}, postgresql_where={has_pg})")
    assert not mismatched, "partial index WHERE differs by dialect: " + ", ".join(mismatched)
