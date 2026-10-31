"""QA fix E2 — the duplicate-phone guard and /clients/duplicates compare numbers, not strings.

Found in the 5-shop QA run (31 Oct 2026): with prohibit_dup_phone on and two contacts already
sharing a phone, creating a third was a 500 (MultipleResultsFound). "(647) 555-0199" and
"+1 647 555 0199" (or "+61 412 555 019" and "+61412555019") were treated as different numbers.
"""
import pytest

from backend.models import Client


async def _org_id(client, h):
    return (await client.get("/api/auth/me", headers=h)).json()["org_id"]


@pytest.mark.asyncio
async def test_create_with_existing_duplicates_is_409_not_500(client, tenant, db):
    org = await _org_id(client, tenant)
    db.add_all([Client(org_id=org, name="A", phone="6475550199"),
                Client(org_id=org, name="B", phone="6475550199")])
    await db.commit()
    assert (await client.put("/api/staff/company", json={"prohibit_dup_phone": True},
                             headers=tenant)).status_code == 200
    r = await client.post("/api/clients", json={"name": "C", "phone": "6475550199"}, headers=tenant)
    assert r.status_code == 409, r.text


@pytest.mark.asyncio
async def test_dup_guard_ignores_formatting(client, tenant):
    await client.put("/api/staff/company", json={"prohibit_dup_phone": True, "country": "CA"},
                     headers=tenant)
    r = await client.post("/api/clients", json={"name": "A", "phone": "(647) 555-0199"}, headers=tenant)
    assert r.status_code == 201, r.text
    r = await client.post("/api/clients", json={"name": "B", "phone": "+1 647 555 0199"}, headers=tenant)
    assert r.status_code == 409, r.text
    # a genuinely different number still goes through
    r = await client.post("/api/clients", json={"name": "C", "phone": "+1 647 555 0198"}, headers=tenant)
    assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_duplicates_report_groups_formatting_variants(client, tenant):
    await client.put("/api/staff/company", json={"country": "AU"}, headers=tenant)
    for name, phone in [("A", "+61 412 555 019"), ("B", "+61412555019"), ("C", "0412 555 019"),
                        ("D", "(647) 555-0199"), ("E", "+1 647 555 0199"), ("F", "0400 000 000")]:
        assert (await client.post("/api/clients", json={"name": name, "phone": phone},
                                  headers=tenant)).status_code == 201
    r = await client.get("/api/clients/duplicates", headers=tenant)
    assert r.status_code == 200, r.text
    clusters = r.json()["items"]
    names = sorted(sorted(c["name"] for c in cl["clients"]) for cl in clusters)
    # AU shop: the national 0412… form is the same number; the +1 one is a different country
    assert names == [["A", "B", "C"]], names
    cl = clusters[0]
    assert cl["count"] == 3 and cl["phone"] and cl["keep_hint_id"]
