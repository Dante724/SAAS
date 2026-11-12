"""B6 — unified tags: CRUD + attach/detach + filter across entities."""
import pytest


@pytest.mark.asyncio
async def test_tag_crud_and_attach(client, tenant):
    h = tenant
    # create
    r = await client.post("/api/tags", json={"name": "VIP", "color": "#f59e0b"}, headers=h)
    assert r.status_code == 201, r.text
    tid = r.json()["id"]

    # duplicate (case-insensitive) blocked
    r = await client.post("/api/tags", json={"name": "vip"}, headers=h)
    assert r.status_code == 409

    # list
    r = await client.get("/api/tags", headers=h)
    assert any(t["id"] == tid for t in r.json()["items"])

    # attach to a contact + an order (real records of this shop: audit SEC-08 refuses made-up ids)
    me = (await client.get("/api/auth/me", headers=h)).json()
    c1 = (await client.post("/api/clients", json={"name": "Tagged"}, headers=h)).json()["id"]
    o1 = (await client.post("/api/orders", json={"client_id": c1, "location_id": me["location_id"],
                                                 "complaint": "x"}, headers=h)).json()["id"]
    for et, eid in (("contact", c1), ("order", o1)):
        r = await client.post("/api/tags/attach", json={
            "tag_id": tid, "entity_type": et, "entity_id": eid}, headers=h)
        assert r.status_code == 201, r.text
    # attaching twice is idempotent
    r = await client.post("/api/tags/attach", json={
        "tag_id": tid, "entity_type": "contact", "entity_id": c1}, headers=h)
    assert r.status_code == 201 and r.json().get("already")

    # tags on the contact
    r = await client.get(f"/api/tags/for?entity_type=contact&entity_id={c1}", headers=h)
    assert [t["id"] for t in r.json()["items"]] == [tid]

    # entities carrying the tag (filter helper)
    r = await client.get(f"/api/tags/entities?tag_id={tid}", headers=h)
    kinds = {(e["entity_type"], e["entity_id"]) for e in r.json()["items"]}
    assert kinds == {("contact", c1), ("order", o1)}

    # bad entity type rejected
    r = await client.post("/api/tags/attach", json={
        "tag_id": tid, "entity_type": "spaceship", "entity_id": "x"}, headers=h)
    assert r.status_code == 422

    # detach
    r = await client.post("/api/tags/detach", json={
        "tag_id": tid, "entity_type": "order", "entity_id": o1}, headers=h)
    assert r.status_code == 200
    r = await client.get(f"/api/tags/entities?tag_id={tid}", headers=h)
    assert len(r.json()["items"]) == 1

    # delete tag removes remaining attachments
    r = await client.delete(f"/api/tags/{tid}", headers=h)
    assert r.status_code == 200
    r = await client.get(f"/api/tags/for?entity_type=contact&entity_id={c1}", headers=h)
    assert r.json()["items"] == []
