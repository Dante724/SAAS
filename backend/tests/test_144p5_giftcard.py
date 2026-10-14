"""144p5 — gift cards: always server-generate a unique code, require a client, optional expiry."""
from datetime import datetime, timedelta


async def _client(client, tenant, name="GC Co"):
    return (await client.post("/api/clients", json={"name": name, "phone": "+15550444"}, headers=tenant)).json()["id"]


async def test_giftcard_autogenerates_unique_code_and_links_client(client, tenant):
    cid = await _client(client, tenant)
    r1 = await client.post("/api/giftcards", json={"value": 50, "client_id": cid}, headers=tenant)
    r2 = await client.post("/api/giftcards", json={"value": 50, "client_id": cid}, headers=tenant)
    assert r1.status_code == 201 and r2.status_code == 201, (r1.text, r2.text)
    c1, c2 = r1.json(), r2.json()
    assert c1["code"] and c1["code"].startswith("GC-")      # server-generated
    assert c1["code"] != c2["code"]                          # unique per issue
    assert c1["client_id"] == cid                            # linked to the client


async def test_giftcard_requires_client(client, tenant):
    r = await client.post("/api/giftcards", json={"value": 25}, headers=tenant)
    assert r.status_code == 422    # client_id now required by the schema


async def test_giftcard_optional_expiry_roundtrips(client, tenant):
    cid = await _client(client, tenant, name="Expiry Co")
    exp = (datetime.utcnow() + timedelta(days=30)).isoformat()
    r = await client.post("/api/giftcards", json={"value": 40, "client_id": cid, "expires_at": exp}, headers=tenant)
    assert r.status_code == 201, r.text
    assert r.json()["expires_at"] is not None
    # no-expiry card comes back null
    r2 = await client.post("/api/giftcards", json={"value": 40, "client_id": cid}, headers=tenant)
    assert r2.json()["expires_at"] is None
