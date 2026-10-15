"""Stock documents (purchase return, write-off, posting) allocate numbers, so they must be
configurable in Settings → Numbering like every other numbered document."""
import pytest

from backend.services.numbering import next_number

STOCK_DOCS = {"purchase_return": "RET-00001", "write_off": "WOF-00001", "posting": "POST-00001"}


@pytest.mark.asyncio
async def test_stock_docs_listed_with_defaults(client, tenant, db):
    items = (await client.get("/api/settings/numbering", headers=tenant)).json()["items"]
    listed = {i["doc_type"]: i["example"] for i in items}
    for dt, example in STOCK_DOCS.items():
        assert listed.get(dt) == example


@pytest.mark.asyncio
async def test_stock_docs_default_numbers(client, tenant, db):
    org = (await client.get("/api/auth/me", headers=tenant)).json()["org_id"]
    for dt, first in STOCK_DOCS.items():
        assert await next_number(db, org, dt) == first


@pytest.mark.asyncio
@pytest.mark.parametrize("doc_type", sorted(STOCK_DOCS))
async def test_stock_docs_configurable(client, tenant, db, doc_type):
    org = (await client.get("/api/auth/me", headers=tenant)).json()["org_id"]
    r = await client.put("/api/settings/numbering",
                         json={"doc_type": doc_type, "template": "SD-{Y}-{C:3}", "begins_from": 10},
                         headers=tenant)
    assert r.status_code == 200, r.text
    n = await next_number(db, org, doc_type)
    assert n.startswith("SD-") and n.endswith("-010")
    await db.commit()
    # and the no-rewind guard covers them too
    r = await client.put("/api/settings/numbering",
                         json={"doc_type": doc_type, "template": "SD-{Y}-{C:3}", "begins_from": 10},
                         headers=tenant)
    assert r.status_code == 422
