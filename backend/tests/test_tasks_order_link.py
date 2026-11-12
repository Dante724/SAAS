"""Linking a task to a work order. The job picker sends an order id the caller typed or picked:
an unknown id used to fail on the foreign key (500), and a technician whose role shows only their
own jobs could link — and then read the number and client of — a job they can't open. Now an id
the caller can't open reads exactly like an unknown one (404), and task payloads never show the
number or client of a job the viewer can't open (tasks linked before this check)."""
import uuid

import pytest
from sqlalchemy import select

from backend.models import Employee, Order, Organisation, Role, Task
from backend.services.auth import hash_password


async def _scoped_tech(client, db, org_id, location_id):
    """A technician whose role sees only their own (or unowned) work orders, and may hand out work
    (so the read checks below aren't masked by task visibility)."""
    role = Role(org_id=org_id, name=f"Bench {uuid.uuid4().hex[:4]}", order_scope="own",
                permissions={"can_assign_staff": True})
    db.add(role)
    await db.flush()
    email = f"tech-{uuid.uuid4().hex[:8]}@link.dev"
    tech = Employee(org_id=org_id, location_id=location_id, role_id=role.id, name="Bench Tech",
                    email=email, password_hash=hash_password("pw-tech-12345"), active=True)
    db.add(tech)
    await db.commit()
    await db.refresh(tech)
    r = await client.post("/api/auth/token", data={"username": email, "password": "pw-tech-12345"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}, tech


@pytest.fixture
async def shop(client, tenant, db):
    """The tenant's shop: an admin, a scoped tech, a job assigned to the admin (hidden from the
    tech) and a job assigned to the tech."""
    org = (await db.execute(select(Organisation))).scalars().first()
    admin = (await db.execute(select(Employee).where(Employee.org_id == org.id))).scalars().first()
    tech_h, tech = await _scoped_tech(client, db, org.id, admin.location_id)
    cid = (await client.post("/api/clients", json={"name": "Hidden Client Hal", "phone": "+15550444"},
                             headers=tenant)).json()["id"]
    hidden = (await client.post("/api/orders", headers=tenant, json={"client_id": cid, "complaint": "a"})).json()
    mine = (await client.post("/api/orders", headers=tenant, json={"client_id": cid, "complaint": "b"})).json()
    for oid, emp in ((hidden["id"], admin), (mine["id"], tech)):
        (await db.execute(select(Order).where(Order.id == oid))).scalar_one().assigned_to_id = emp.id
    await db.commit()
    return dict(org=org, admin=admin, tech=tech, tech_h=tech_h, hidden=hidden, mine=mine)


def _msg(r):
    d = r.json().get("detail")
    return d.get("message") if isinstance(d, dict) else d


# ── writes ────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_create_with_unknown_order_is_404_not_500(client, tenant, db, shop):
    r = await client.post("/api/tasks", json={"title": "x", "order_id": "no-such-order"}, headers=tenant)
    assert r.status_code == 404, r.text
    assert "work order" in _msg(r)
    assert (await db.execute(select(Task))).scalars().all() == []


@pytest.mark.asyncio
async def test_create_with_another_shops_order_is_404(client, tenant, db, shop):
    r = await client.post("/api/auth/register", json={
        "org_name": "Other", "name": "O", "country": "US",
        "email": f"o-{uuid.uuid4().hex[:6]}@x.dev", "password": "pw-test-12345"})
    other = {"Authorization": f"Bearer {r.json()['access_token']}"}
    ocid = (await client.post("/api/clients", json={"name": "Theirs"}, headers=other)).json()["id"]
    theirs = (await client.post("/api/orders", headers=other, json={"client_id": ocid, "complaint": "z"})).json()
    r = await client.post("/api/tasks", json={"title": "x", "order_id": theirs["id"]}, headers=tenant)
    assert r.status_code == 404, r.text


@pytest.mark.asyncio
async def test_create_linking_a_job_the_caller_cant_open_is_404(client, shop):
    h = shop["tech_h"]
    r = await client.post("/api/tasks", json={"title": "x", "order_id": shop["hidden"]["id"]}, headers=h)
    assert r.status_code == 404, r.text
    unknown = await client.post("/api/tasks", json={"title": "x", "order_id": "nope"}, headers=h)
    assert _msg(r) == _msg(unknown)          # reads exactly like an unknown id
    ok = await client.post("/api/tasks", json={"title": "y", "order_id": shop["mine"]["id"]}, headers=h)
    assert ok.status_code == 201, ok.text
    assert ok.json()["order_number"] == shop["mine"]["order_number"]


@pytest.mark.asyncio
async def test_patch_order_link_checks_the_job(client, tenant, shop):
    h = shop["tech_h"]
    t = (await client.post("/api/tasks", json={"title": "t"}, headers=h)).json()
    for bad in ("no-such-order", shop["hidden"]["id"]):
        r = await client.patch(f"/api/tasks/{t['id']}", json={"order_id": bad}, headers=h)
        assert r.status_code == 404, (bad, r.text)
    r = await client.patch(f"/api/tasks/{t['id']}", json={"order_id": shop["mine"]["id"]}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["order_id"] == shop["mine"]["id"]
    # an empty link still unlinks
    r = await client.patch(f"/api/tasks/{t['id']}", json={"order_id": ""}, headers=h)
    assert r.status_code == 200 and r.json()["order_id"] is None
    # the admin may link any of the shop's jobs
    r = await client.patch(f"/api/tasks/{t['id']}", json={"order_id": shop["hidden"]["id"]}, headers=tenant)
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_patch_other_fields_of_a_task_linked_to_a_hidden_job_still_works(client, tenant, shop):
    """The link isn't re-checked when it isn't being changed (a manager linked it)."""
    t = (await client.post("/api/tasks", json={"title": "t", "order_id": shop["hidden"]["id"],
                                               "assigned_to_id": shop["tech"].id}, headers=tenant)).json()
    r = await client.patch(f"/api/tasks/{t['id']}", json={"title": "renamed", "order_id": shop["hidden"]["id"]},
                           headers=shop["tech_h"])
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "renamed"
    assert r.json()["order_number"] is None      # still not shown to the tech


# ── reads: a task linked to a job the viewer can't open ───────────────────────
@pytest.mark.asyncio
async def test_task_reads_hide_the_number_and_client_of_a_job_the_viewer_cant_open(client, tenant, shop):
    hidden = shop["hidden"]
    t = (await client.post("/api/tasks", json={"title": "Linked by the boss", "order_id": hidden["id"],
                                               "assigned_to_id": shop["tech"].id}, headers=tenant)).json()
    assert t["order_number"] == hidden["order_number"]
    h = shop["tech_h"]

    one = (await client.get(f"/api/tasks/{t['id']}", headers=h)).json()
    assert one["order_number"] is None and one.get("client_name") is None
    assert one["order_id"] == hidden["id"]       # the id alone reveals nothing; opening it is refused

    row = next(x for x in (await client.get("/api/tasks", headers=h)).json()["items"] if x["id"] == t["id"])
    assert row["order_number"] is None and row["client_name"] is None

    day = (await client.get("/api/tasks/my-day", headers=h)).json()
    rows = [x for k in ("catch_up", "today", "upcoming", "later") for x in day.get(k, []) if x["id"] == t["id"]]
    assert rows and rows[0]["order_number"] is None and rows[0]["client_name"] is None

    # searching by the hidden job's number or client doesn't find it either
    for term in (hidden["order_number"], "Hidden Client"):
        found = (await client.get("/api/tasks", params={"q": term}, headers=h)).json()["items"]
        assert t["id"] not in {x["id"] for x in found}, term

    # the admin sees everything
    adm = (await client.get(f"/api/tasks/{t['id']}", headers=tenant)).json()
    assert adm["order_number"] == hidden["order_number"]
    row = next(x for x in (await client.get("/api/tasks", headers=tenant)).json()["items"] if x["id"] == t["id"])
    assert (row["order_number"], row["client_name"]) == (hidden["order_number"], "Hidden Client Hal")
    found = (await client.get("/api/tasks", params={"q": hidden["order_number"]}, headers=tenant)).json()["items"]
    assert t["id"] in {x["id"] for x in found}


@pytest.mark.asyncio
async def test_task_on_the_techs_own_job_shows_number_and_client(client, tenant, shop):
    mine = shop["mine"]
    t = (await client.post("/api/tasks", json={"title": "Mine", "order_id": mine["id"],
                                               "assigned_to_id": shop["tech"].id}, headers=tenant)).json()
    row = next(x for x in (await client.get("/api/tasks", headers=shop["tech_h"])).json()["items"]
               if x["id"] == t["id"])
    assert (row["order_number"], row["client_name"]) == (mine["order_number"], "Hidden Client Hal")
