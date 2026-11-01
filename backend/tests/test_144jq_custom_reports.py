"""144jq — custom report builder: whitelist safety, org isolation, aggregates, API."""
import uuid
from datetime import datetime

import pytest

from backend.models import Invoice
from backend.services import custom_reports as cr


async def _org(client, tenant):
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    return me.get("org_id") or me.get("orgId")


def test_sources_meta_is_safe():
    m = cr.sources_meta()
    assert {"orders", "invoices", "payments", "clients", "products"} <= set(m)
    # never leak SQLAlchemy models / internal attrs to the client
    for s in m.values():
        assert "model" not in s and isinstance(s["fields"], list)


async def test_rejects_unknown_source(client, tenant, db):
    org = await _org(client, tenant)
    with pytest.raises(ValueError):
        await cr.build_and_run(db, org, {"source": "employees"})   # not whitelisted
    with pytest.raises(ValueError):
        await cr.build_and_run(db, org, {"source": "invoices; drop table"})


async def test_rejects_bad_filter_field_and_op(client, tenant, db):
    org = await _org(client, tenant)
    with pytest.raises(ValueError):
        await cr.build_and_run(db, org, {"source": "invoices", "filters": [{"field": "org_id", "op": "eq", "value": "x"}]})
    with pytest.raises(ValueError):   # gt not allowed on a text field
        await cr.build_and_run(db, org, {"source": "invoices", "filters": [{"field": "status", "op": "gt", "value": "x"}]})


async def test_isolation_flat_and_group_aggregate(client, tenant, db):
    a = await _org(client, tenant)
    r = await client.post("/api/auth/register", json={
        "org_name": "Shop B", "name": "B", "email": f"b-{uuid.uuid4().hex[:8]}@t.dev", "password": "pw-test-12345"})
    b = (await client.get("/api/auth/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"})).json()["org_id"]
    assert a != b

    for org, total, status in [(a, 100, "paid"), (a, 50, "paid"), (a, 30, "draft"), (b, 999, "paid")]:
        db.add(Invoice(id=uuid.uuid4().hex, org_id=org, invoice_number=f"X-{uuid.uuid4().hex[:6]}", total=total, status=status,
                       issued_at=datetime(2026, 9, 10)))
    await db.commit()

    # flat: org A sees only its 3 rows; B's 999 never appears
    flat = await cr.build_and_run(db, a, {"source": "invoices", "columns": ["total", "status"]})
    totals = sorted(row[0] for row in flat["rows"])
    assert totals == [30, 50, 100] and 999 not in totals

    # group by status → sum(total) + count, org-scoped
    g = await cr.build_and_run(db, a, {"source": "invoices", "group_by": "status",
                                       "aggregates": [{"fn": "sum", "field": "total"}, {"fn": "count"}]})
    by = {row[0]: (row[1], row[2]) for row in g["rows"]}
    assert by["paid"] == (150, 2)
    assert by["draft"] == (30, 1)


async def test_api_crud_and_run(client, tenant):
    r = await client.post("/api/custom-reports", headers=tenant,
                          json={"name": "My invoices", "config": {"source": "invoices", "columns": ["total", "status"]}})
    assert r.status_code == 200, r.text
    rid = r.json()["id"]

    lst = (await client.get("/api/custom-reports", headers=tenant)).json()
    assert any(x["id"] == rid for x in lst["items"])

    run = await client.get(f"/api/custom-reports/{rid}/run", headers=tenant)
    assert run.status_code == 200 and "columns" in run.json() and run.json()["title"] == "My invoices"

    # unknown source is rejected at create and at ad-hoc run
    assert (await client.post("/api/custom-reports", headers=tenant, json={"name": "x", "config": {"source": "nope"}})).status_code == 422
    assert (await client.post("/api/custom-reports/run", headers=tenant, json={"config": {"source": "nope"}})).status_code == 422

    assert (await client.delete(f"/api/custom-reports/{rid}", headers=tenant)).status_code == 200
    assert (await client.get(f"/api/custom-reports/{rid}/run", headers=tenant)).status_code == 404


# ── 144ee: friendly validation messages (code-review #5) ──
async def test_friendly_error_on_bad_numeric_filter_value(client, tenant, db):
    org = await _org(client, tenant)
    # a non-numeric value on a money field must NOT surface a raw "could not convert
    # string to float" — the message should name the field and say what's wrong.
    with pytest.raises(ValueError) as e:
        await cr.build_and_run(db, org, {"source": "invoices",
                                         "filters": [{"field": "total", "op": "gt", "value": "abc"}]})
    msg = str(e.value).lower()
    assert "total" in msg and "valid" in msg
    assert "could not convert" not in msg


async def test_friendly_error_on_bad_date_range(client, tenant, db):
    org = await _org(client, tenant)
    with pytest.raises(ValueError) as e:
        await cr.build_and_run(db, org, {"source": "invoices"}, date_from="13/09/2026")
    assert "date" in str(e.value).lower()


async def test_bad_numeric_filter_returns_422_not_500(client, tenant):
    # end-to-end: the friendly ValueError maps to a 422, never an unhandled 500.
    r = await client.post("/api/custom-reports/run", headers=tenant,
                          json={"config": {"source": "invoices",
                                           "filters": [{"field": "total", "op": "gt", "value": "abc"}]}})
    assert r.status_code == 422, r.text


async def test_grouped_run_ignores_stale_columns(client, tenant, db):
    """144ee backward-compat: a saved grouped report from BEFORE the fix still carries a
    `columns` list in its config. Grouped mode must ignore it and return [group_key, agg],
    never widen the output by those columns. Locks the contract against a future refactor."""
    org = await _org(client, tenant)
    for total, status in [(10, "paid"), (20, "paid"), (5, "draft")]:
        db.add(Invoice(id=uuid.uuid4().hex, org_id=org, invoice_number=f"X-{uuid.uuid4().hex[:6]}", total=total,
                       status=status, issued_at=datetime(2026, 9, 10)))
    await db.commit()
    out = await cr.build_and_run(db, org, {
        "source": "invoices",
        "columns": ["invoice_number", "subtotal", "tax", "total"],   # stale, must be ignored
        "group_by": "status",
        "aggregates": [{"fn": "count"}],
    })
    assert [c["key"] for c in out["columns"]] == ["status", "count"]   # exactly 2, not 6
    by = {row[0]: row[1] for row in out["rows"]}
    assert by.get("paid") == 2 and by.get("draft") == 1


async def test_date_filter_value_is_validated(client, tenant, db):
    """144ef (code-review C): date-type filter values are parsed; a good date runs, a
    malformed one gives the friendly field-named message instead of leaking to SQL."""
    org = await _org(client, tenant)
    ok = await cr.build_and_run(db, org, {"source": "invoices",
                                          "filters": [{"field": "issued_at", "op": "gte", "value": "2026-01-01"}]})
    assert "rows" in ok
    with pytest.raises(ValueError) as e:
        await cr.build_and_run(db, org, {"source": "invoices",
                                         "filters": [{"field": "issued_at", "op": "gte", "value": "13/09/2026"}]})
    msg = str(e.value).lower()
    assert "issued" in msg and "valid" in msg
