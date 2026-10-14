"""Postgres-only failures found by the backend-postgres CI job (SQLite never enforced them).

- employee_balance_ledger.idempotency_key is VARCHAR(120) but per-line commission keys are three
  UUIDs plus an event (~126 chars): every such accrual failed on Postgres.
- tasks.priority is text but the API sends numbers: asyncpg rejected the int, so creating any task
  returned 500.
"""
import pytest

from backend.models import EmployeeBalanceLedger, Task

UUID = "123e4567-e89b-12d3-a456-426614174000"


def test_long_ledger_keys_fit_the_column_and_stay_stable():
    raw = f"status_changed:{UUID}:{UUID}:{UUID}"
    assert len(raw) > 120
    k1, k2 = EmployeeBalanceLedger.normalize_key(raw), EmployeeBalanceLedger.normalize_key(raw)
    assert k1 == k2 and len(k1) <= 120                         # deterministic → idempotency holds
    assert EmployeeBalanceLedger.normalize_key(raw + "x") != k1
    assert EmployeeBalanceLedger(idempotency_key=raw).idempotency_key == k1
    short = f"base:{UUID}"
    assert EmployeeBalanceLedger.normalize_key(short) == short   # existing rows keep matching


def test_task_priority_is_stored_as_text():
    assert Task(priority=2).priority == "2"


@pytest.mark.asyncio
async def test_task_api_round_trips_numeric_priority(client, tenant):
    r = await client.post("/api/tasks", json={"title": "x", "priority": 2}, headers=tenant)
    assert r.status_code == 201, r.text
    assert r.json()["priority"] == 2
