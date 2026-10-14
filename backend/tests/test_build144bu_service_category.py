"""Build 144bu — services join the shared category tree (like products/bundles),
and the public appointment config exposes each service's category so the wizard can
group services by category."""
import uuid


async def _tenant(client):
    email = f"admin-{uuid.uuid4().hex[:8]}@test.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Bu Co", "name": "Owner", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _cat(client, H, name):
    r = await client.post("/api/catalog/categories", json={"entity_type": "service", "name": name}, headers=H)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_service_carries_category(client):
    H = await _tenant(client)
    cid = await _cat(client, H, "Screens")
    r = await client.post("/api/catalog/services", json={
        "name": "Screen replace", "category_id": cid, "price": 60}, headers=H)
    assert r.status_code == 201, r.text
    assert r.json()["category_id"] == cid
    assert r.json()["category_name"] == "Screens"


async def test_filter_services_by_category(client):
    H = await _tenant(client)
    a = await _cat(client, H, "Screens")
    b = await _cat(client, H, "Batteries")
    await client.post("/api/catalog/services", json={"name": "Glass", "category_id": a}, headers=H)
    await client.post("/api/catalog/services", json={"name": "Cell", "category_id": b}, headers=H)
    got = (await client.get(f"/api/catalog/services?category_id={a}", headers=H)).json()["items"]
    assert [s["name"] for s in got] == ["Glass"], got


async def test_category_tree_counts_services(client):
    H = await _tenant(client)
    cid = await _cat(client, H, "Screens")
    await client.post("/api/catalog/services", json={"name": "Glass", "category_id": cid}, headers=H)
    tree = (await client.get("/api/catalog/categories?entity_type=service", headers=H)).json()["tree"]
    node = [n for n in tree if n["id"] == cid][0]
    assert node["count"] >= 1, tree


async def test_appointment_config_exposes_service_category(client):
    H = await _tenant(client)
    slug = (await client.get("/api/staff/public-pages", headers=H)).json()["slug"]
    cid = await _cat(client, H, "Screens")
    await client.post("/api/catalog/services", json={"name": "Screen replace", "category_id": cid, "price": 60}, headers=H)
    cfg = (await client.get(f"/api/public/appointment/{slug}")).json()
    svc = [s for s in cfg["services"] if s["name"] == "Screen replace"][0]
    assert svc["category"] == "Screens", svc
