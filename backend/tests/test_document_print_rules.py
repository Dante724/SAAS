"""Build 144o — per-status print permissions + Legal-entity / Payer variables."""
import pytest


async def _order(client, h):
    await client.get("/api/statuses", headers=h)  # ensure statuses seeded
    loc = (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]
    cid = (await client.post("/api/clients", json={"name": "Ray", "phone": "+15550021"},
                             headers=h)).json()["id"]
    oid = (await client.post("/api/orders", json={
        "client_id": cid, "location_id": loc, "complaint": "x"}, headers=h)).json()["id"]
    return oid, cid


@pytest.mark.asyncio
async def test_new_variable_groups_present(client, tenant):
    d = (await client.get("/api/documents/templates", headers=tenant)).json()
    groups = {g["group"] for g in d["variable_groups"]}
    assert "Legal entity" in groups and "Payer" in groups
    flat = d["variables"]
    for v in ("{Payer Name}", "{Payer Email}", "{Legal Entity Name}", "{Legal Entity Tax ID}"):
        assert v in flat, v


@pytest.mark.asyncio
async def test_print_statuses_gate_the_order_print_list(client, tenant):
    oid, _ = await _order(client, tenant)
    status = (await client.get(f"/api/orders/{oid}", headers=tenant)).json()["status"]

    # doc A: any status (empty list) — always available
    await client.post("/api/documents/templates", json={
        "name": "Any", "doc_type": "work_order", "body_html": "a", "print_statuses": []}, headers=tenant)
    # doc B: restricted to a status the order is NOT in
    await client.post("/api/documents/templates", json={
        "name": "OnlyClosed", "doc_type": "work_order", "body_html": "b",
        "print_statuses": ["done", "delivered"]}, headers=tenant)
    # doc C: restricted to the order's current status
    await client.post("/api/documents/templates", json={
        "name": "OnlyNow", "doc_type": "work_order", "body_html": "c",
        "print_statuses": [status]}, headers=tenant)

    # unfiltered listing shows all three
    alld = (await client.get("/api/documents/templates", headers=tenant)).json()["items"]
    assert {"Any", "OnlyClosed", "OnlyNow"} <= {t["name"] for t in alld}
    # round-trip of print_statuses
    onlyclosed = next(t for t in alld if t["name"] == "OnlyClosed")
    assert onlyclosed["print_statuses"] == ["done", "delivered"]

    # filtered to the order → only "Any" and "OnlyNow" (not the closed-only one)
    names = {t["name"] for t in (await client.get(
        f"/api/documents/templates?order_id={oid}", headers=tenant)).json()["items"]}
    assert "Any" in names and "OnlyNow" in names
    assert "OnlyClosed" not in names


@pytest.mark.asyncio
async def test_payer_variable_resolves_to_designated_payer(client, tenant):
    # payer is a different client than the order's client
    payer = (await client.post("/api/clients", json={
        "name": "Big Corp", "phone": "+15550099", "email": "ap@bigcorp.example"}, headers=tenant)).json()["id"]
    cid = (await client.post("/api/clients", json={
        "name": "End User", "phone": "+15550088", "payer_id": payer}, headers=tenant)).json()["id"]
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    oid = (await client.post("/api/orders", json={
        "client_id": cid, "location_id": loc, "complaint": "x"}, headers=tenant)).json()["id"]

    tpl = (await client.post("/api/documents/templates", json={
        "name": "Bill", "doc_type": "invoice",
        "body_html": "Bill to {Payer Name} ({Payer Email})"}, headers=tenant)).json()["id"]
    html = (await client.get(f"/api/documents/render/{oid}/{tpl}", headers=tenant)).text
    assert "Big Corp" in html
    assert "ap@bigcorp.example" in html
