"""PUT /settings/numbering must never move a counter back onto numbers that are already issued."""
import pytest

from backend.models import Sale
from backend.services.numbering import _counter_pattern, next_number


async def _org(client, h):
    return (await client.get("/api/auth/me", headers=h)).json()["org_id"]


async def _put(client, h, **body):
    return await client.put("/api/settings/numbering", json={"doc_type": "order", **body}, headers=h)


def test_counter_pattern():
    p = _counter_pattern("WO-{Y}-{C:4}")
    assert p.match("WO-2026-0042").group(1) == "0042"
    assert p.match("WO-2026-12345").group(1) == "12345"   # counter outgrew its padding
    assert not p.match("INV-2026-0042") and not p.match("WO-2026-0042x")


@pytest.mark.asyncio
async def test_resave_cannot_rewind_existing_sequence(client, tenant, db):
    org = await _org(client, tenant)
    assert (await _put(client, tenant, template="WO-{C:4}", begins_from=1)).status_code == 200
    for _ in range(3):
        await next_number(db, org, "order")
    await db.commit()

    r = await _put(client, tenant, template="WO-{C:4}", begins_from=1)
    assert r.status_code == 422
    assert "WO-0003" in r.json()["detail"] and "4 or higher" in r.json()["detail"]
    r = await _put(client, tenant, template="WO-{C:4}", begins_from=3)    # at the last issued
    assert r.status_code == 422

    # Moving forward (or saving the current next value with a new template) is fine.
    assert (await _put(client, tenant, template="JOB-{C:4}", begins_from=4)).status_code == 200
    assert await next_number(db, org, "order") == "JOB-0004"


@pytest.mark.asyncio
async def test_first_configuration_respects_documents_already_issued(client, tenant, db):
    """No sequence row for the scope yet, but documents with matching numbers exist."""
    org = await _org(client, tenant)
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    db.add(Sale(org_id=org, location_id=loc, sale_number="POS-0041", seller_id=me["id"]))
    await db.commit()

    r = await client.put("/api/settings/numbering",
                         json={"doc_type": "sale", "template": "POS-{C:4}", "begins_from": 1}, headers=tenant)
    assert r.status_code == 422 and "POS-0041" in r.json()["detail"]
    r = await client.put("/api/settings/numbering",
                         json={"doc_type": "sale", "template": "POS-{C:4}", "begins_from": 42}, headers=tenant)
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_list_reports_real_next_value_for_unconfigured(client, tenant, db):
    org = await _org(client, tenant)
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    db.add(Sale(org_id=org, location_id=loc, sale_number="S-00007", seller_id=me["id"]))
    await db.commit()
    items = (await client.get("/api/settings/numbering", headers=tenant)).json()["items"]
    sale = next(i for i in items if i["doc_type"] == "sale")
    assert sale["is_default"] and sale["next_value"] == 8
    # and saving that row unchanged is accepted
    r = await client.put("/api/settings/numbering", json={
        "doc_type": "sale", "template": sale["template"], "begins_from": sale["next_value"]}, headers=tenant)
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_begins_from_must_be_positive(client, tenant):
    assert (await _put(client, tenant, template="WO-{C}", begins_from=0)).status_code == 422
