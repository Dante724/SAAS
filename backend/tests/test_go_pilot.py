"""Tekly Go shop pilot (docs/pilot/tekly-go-shop-pilot.md).

- requests carrying X-Tekly-Client: go/<v> mark the status changes, customer messages and photos
  they make; everything else is "web"
- scanning a pilot label (a /go/job/<id> link) or typing a WO number resolves to the job, and only
  inside the scanner's own org; every scan is logged as a lookup without the code
- web searches are logged by kind (name / phone / WO / last 4), never by their text, and a typing
  burst counts once
- QR job labels: a PDF for picked jobs or one person's open jobs; needs can_create_tickets; another
  org's ids are a 404
- the pilot report: per-employee counts, the headline share against the 50% rule, CSV; needs
  can_manage_settings; another org's ids are a 404
"""
import uuid

import pytest
from sqlalchemy import select

from backend.models import AuditLog, OrderComment, OrderStatusLog
from backend.services import pilot
from backend.tests.test_sensitive_reads import _restricted

GO = {"X-Tekly-Client": "go/pwa-test"}
PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="


async def _job(client, h, name="Maria Lopez", brand="Apple", model="iPhone 14"):
    cid = (await client.post("/api/clients", headers=h, json={
        "name": name, "phone": f"+1555{uuid.uuid4().int % 10**7:07d}"})).json()["id"]
    body = {"client_id": cid, "complaint": "screen"}
    r = await client.post("/api/assets", headers=h, json={"client_id": cid, "brand": brand, "model": model})
    if r.status_code in (200, 201):
        body["device_id"] = r.json()["id"]
    r = await client.post("/api/orders", headers=h, json=body)
    assert r.status_code == 201, r.text
    return r.json()


async def _statuses(client, h):
    items = (await client.get("/api/statuses?record_type=work_order", headers=h)).json()["items"]
    return [s["key"] for s in items if not s.get("is_terminal")]


async def _other_org(client):
    email = f"other-{uuid.uuid4().hex[:8]}@test.dev"
    reg = await client.post("/api/auth/register", json={
        "org_name": "Other Co", "name": "Other", "email": email, "password": "pw-test-12345"})
    return {"Authorization": f"Bearer {reg.json()['access_token']}"}


# ── where an action came from ────────────────────────────────────────────────

def test_classify_query():
    c = pilot.classify_query
    assert [c(x) for x in ("Maria", "maría lópez", "WO-00012", "wo12", "#123", "123")] == \
        ["name", "name", "wo", "wo", "wo", "wo"]
    assert [c(x) for x in ("4412", "07700 900123", "+44 (7700) 900-123", "a@b.co", "", "--")] == \
        ["last4", "phone", "phone", "email", "other", "other"]


def test_client_header_is_short_and_inert():
    from backend.core.client_source import parse_client
    assert parse_client("go/1.2.3") == "go/1.2.3"
    assert parse_client(None) == "web"
    assert parse_client("go/<script>") == "web"
    assert parse_client("go/" + "x" * 200) == "web"


@pytest.mark.asyncio
async def test_status_changes_messages_and_photos_remember_the_app(client, db, tenant):
    o = await _job(client, tenant)
    a, b = (await _statuses(client, tenant))[1:3]
    assert (await client.patch(f"/api/orders/{o['id']}/status", headers={**tenant, **GO}, json={"status": a})).status_code == 200
    assert (await client.patch(f"/api/orders/{o['id']}/status", headers=tenant, json={"status": b})).status_code == 200
    logs = (await db.execute(select(OrderStatusLog).where(OrderStatusLog.order_id == o["id"])
                             .order_by(OrderStatusLog.changed_at))).scalars().all()
    assert [(x.to_status, x.client) for x in logs[1:]] == [(a, "go/pwa-test"), (b, "web")]

    assert (await client.post(f"/api/orders/{o['id']}/thread", headers={**tenant, **GO}, json={"body": "Ready!"})).status_code == 201
    c = (await db.execute(select(OrderComment).where(OrderComment.order_id == o["id"]))).scalars().one()
    assert c.client == "go/pwa-test"

    m = (await client.post("/api/media", headers=tenant, json={"data": PNG})).json()["id"]
    r = await client.post(f"/api/orders/{o['id']}/intake-photos/add", headers={**tenant, **GO}, json={"photos": [m]})
    assert r.status_code == 200
    # a retry adds nothing, so it counts nothing
    await client.post(f"/api/orders/{o['id']}/intake-photos/add", headers={**tenant, **GO}, json={"photos": [m]})
    rows = (await db.execute(select(AuditLog).where(AuditLog.action == pilot.PHOTOS))).scalars().all()
    assert [(x.client, x.diff["count"]) for x in rows] == [("go/pwa-test", 1)]


# ── scans and searches ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_scanning_a_label_link_or_wo_number_finds_the_job(client, db, tenant):
    o = await _job(client, tenant)
    for code in (f"https://shop.example.com/go/job/{o['id']}?via=qr", f"/go/job/{o['id']}", o["order_number"]):
        r = (await client.post("/api/scan/resolve", headers={**tenant, **GO}, json={"code": code})).json()
        assert r["matched"] and r["type"] == "order" and r["id"] == o["id"], code
    rows = (await db.execute(select(AuditLog).where(AuditLog.action == pilot.LOOKUP)
                             .order_by(AuditLog.created_at))).scalars().all()
    assert [(x.diff["via"], x.diff["by"], x.client, x.entity_id) for x in rows] == \
        [("scan", "link", "go/pwa-test", o["id"])] * 2 + [("scan", "wo", "go/pwa-test", o["id"])]
    assert all(o["order_number"] not in str(x.diff) and o["id"] not in str(x.diff) for x in rows)


@pytest.mark.asyncio
async def test_a_label_from_another_org_does_not_resolve(client, tenant):
    o = await _job(client, tenant)
    other = await _other_org(client)
    r = (await client.post("/api/scan/resolve", headers=other, json={"code": f"https://x/go/job/{o['id']}"})).json()
    assert r["matched"] is False


@pytest.mark.asyncio
async def test_searches_are_logged_by_kind_not_text(client, db, tenant):
    await _job(client, tenant, name="Maria Lopez")
    await client.get("/api/orders?search=Mar", headers=tenant)
    await client.get("/api/orders?search=Maria", headers=tenant)          # same burst → one lookup
    await client.get("/api/orders?q=4412", headers=tenant)               # other box → its own
    await client.get("/api/orders?search=Maria&skip=50", headers=tenant)  # paging isn't a lookup
    await client.get("/api/orders", headers=tenant)                       # nor is no search
    rows = (await db.execute(select(AuditLog).where(AuditLog.action == pilot.LOOKUP)
                             .order_by(AuditLog.created_at))).scalars().all()
    assert [(x.diff["by"], x.diff["surface"], x.client) for x in rows] == \
        [("name", "orders_list", "web"), ("last4", "search_box", "web")]
    assert not any("Mar" in str(x.diff) or "4412" in str(x.diff) for x in rows)


# ── QR job labels ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_job_labels_pdf_and_marks_pilot_jobs(client, db, tenant):
    a, b = await _job(client, tenant), await _job(client, tenant, name="Sam")
    r = await client.get(f"/api/pilot/job-labels?order_ids={a['id']},{b['id']}", headers=tenant)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf" and r.content.startswith(b"%PDF")
    marked = (await db.execute(select(AuditLog.entity_id).where(AuditLog.action == pilot.LABELS))).scalars().all()
    assert sorted(marked) == sorted([a["id"], b["id"]])
    assert (await client.get("/api/pilot/job-labels?paper=letter&order_ids=" + a["id"], headers=tenant)).status_code == 200
    assert (await client.get("/api/pilot/job-labels", headers=tenant)).status_code == 400


@pytest.mark.asyncio
async def test_job_labels_for_one_persons_open_jobs(client, db, tenant):
    from backend.models import Employee
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    o = await _job(client, tenant)
    assert (await client.get(f"/api/pilot/job-labels?assigned_to_id={me['id']}", headers=tenant)).status_code == 404
    emp = (await db.execute(select(Employee).where(Employee.id == me["id"]))).scalar_one()
    from backend.models import Order
    order = (await db.execute(select(Order).where(Order.id == o["id"]))).scalar_one()
    order.assigned_to_id = emp.id
    await db.commit()
    r = await client.get(f"/api/pilot/job-labels?assigned_to_id={me['id']}", headers=tenant)
    assert r.status_code == 200 and r.content.startswith(b"%PDF")


def test_label_content():
    assert pilot.job_link("https://shop.example.com/", "abc") == "https://shop.example.com/go/job/abc?via=qr"
    assert pilot.first_name("Maria Lopez") == "Maria" and pilot.first_name(None) == ""
    pdf = pilot.render_job_labels([{"url": "https://x/go/job/1?via=qr", "number": "WO-00001",
                                    "name": "Maria", "device": "Apple iPhone 14 Pro Max with a long name"}] * 25)
    assert pdf.startswith(b"%PDF") and pdf.count(b"/Type /Page\n") + pdf.count(b"/Type /Page\r") + pdf.count(b"/Type /Page ") >= 1


@pytest.mark.asyncio
async def test_job_labels_need_can_create_tickets(client, db, tenant):
    o = await _job(client, tenant)
    h, _, _ = await _restricted(client, db, ["can_create_tickets"])
    assert (await client.get(f"/api/pilot/job-labels?order_ids={o['id']}", headers=h)).status_code == 403


@pytest.mark.asyncio
async def test_job_labels_404_for_another_orgs_job(client, tenant):
    o = await _job(client, tenant)
    other = await _other_org(client)
    assert (await client.get(f"/api/pilot/job-labels?order_ids={o['id']}", headers=other)).status_code == 404
    mine = await _job(client, other)
    assert (await client.get(f"/api/pilot/job-labels?order_ids={mine['id']},{o['id']}", headers=other)).status_code == 404
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    assert (await client.get(f"/api/pilot/job-labels?assigned_to_id={me['id']}", headers=other)).status_code == 404


# ── the report ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_report_counts_phone_vs_all_and_applies_the_rule(client, db, tenant):
    a, b, outside = await _job(client, tenant), await _job(client, tenant), await _job(client, tenant)
    await client.get(f"/api/pilot/job-labels?order_ids={a['id']},{b['id']}", headers=tenant)
    s1, s2 = (await _statuses(client, tenant))[1:3]
    # three status changes on the phone, one at the desk, one on a job without a label
    await client.patch(f"/api/orders/{a['id']}/status", headers={**tenant, **GO}, json={"status": s1})
    await client.patch(f"/api/orders/{a['id']}/status", headers={**tenant, **GO}, json={"status": s2})
    await client.patch(f"/api/orders/{b['id']}/status", headers={**tenant, **GO}, json={"status": s1})
    await client.patch(f"/api/orders/{b['id']}/status", headers=tenant, json={"status": s2})
    await client.patch(f"/api/orders/{outside['id']}/status", headers=tenant, json={"status": s1})
    m = (await client.post("/api/media", headers=tenant, json={"data": PNG})).json()["id"]
    await client.post(f"/api/orders/{a['id']}/intake-photos/add", headers={**tenant, **GO}, json={"photos": [m]})
    await client.post(f"/api/orders/{a['id']}/thread", headers={**tenant, **GO}, json={"body": "Ready"})
    await client.post("/api/scan/resolve", headers={**tenant, **GO}, json={"code": f"/go/job/{a['id']}"})
    await client.get("/api/orders?search=07700900123", headers=tenant)

    rep = (await client.get("/api/pilot/go-report", headers=tenant)).json()
    assert rep["jobs"]["source"] == "labelled" and rep["jobs"]["count"] == 2
    h = rep["headline"]
    assert (h["status_changes_total"], h["status_changes_go"], h["share_status_changes_on_phone"]) == (4, 3, 0.75)
    assert h["verdict"] == "scan_and_timer" and h["threshold"] == 0.5
    (me,) = rep["employees"]
    assert me["photos"] == {"total": 1, "go": 1} and me["messages"] == {"total": 1, "go": 1}
    assert me["scans"] == {"total": 1, "on_pilot_jobs": 1}
    assert me["counter_lookups"]["phone"] == 1 and me["counter_lookups"]["total"] == 1

    # the given jobs instead of the labelled ones: one desk change out of one → the counter wins
    rep = (await client.get(f"/api/pilot/go-report?order_ids={outside['id']}", headers=tenant)).json()
    assert rep["headline"]["share_status_changes_on_phone"] == 0.0
    assert rep["headline"]["verdict"] == "counter_and_owner"

    csv = await client.get("/api/pilot/go-report?format=csv", headers=tenant)
    assert csv.status_code == 200 and csv.headers["content-type"].startswith("text/csv")
    lines = csv.text.splitlines()
    assert lines[0].startswith("employee,status_changes_total,status_changes_go,share_go")
    assert any(x.startswith("ALL STAFF,4,3,0.75") for x in lines)
    assert any("scan_and_timer" in x for x in lines)


@pytest.mark.asyncio
async def test_report_range_and_headline_people(client, tenant):
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    rep = (await client.get(f"/api/pilot/go-report?from=2026-01-01&to=2026-01-02&employee_ids={me['id']}", headers=tenant)).json()
    assert rep["range"]["from"] == "2026-01-01" and rep["headline"]["verdict"] == "not_enough_data"
    assert rep["headline"]["employees_counted"] == "selected"
    assert (await client.get("/api/pilot/go-report?from=2026-02-01&to=2026-01-01", headers=tenant)).status_code == 422
    assert (await client.get("/api/pilot/go-report?from=2025-01-01&to=2026-01-01", headers=tenant)).status_code == 422


@pytest.mark.asyncio
async def test_report_needs_can_manage_settings(client, db, tenant):
    h, _, _ = await _restricted(client, db, ["can_manage_settings"])
    assert (await client.get("/api/pilot/go-report", headers=h)).status_code == 403
    assert (await client.get("/api/pilot/go-report?format=csv", headers=h)).status_code == 403


@pytest.mark.asyncio
async def test_report_404_for_another_orgs_ids(client, tenant):
    o = await _job(client, tenant)
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    other = await _other_org(client)
    assert (await client.get(f"/api/pilot/go-report?order_ids={o['id']}", headers=other)).status_code == 404
    assert (await client.get(f"/api/pilot/go-report?employee_ids={me['id']}", headers=other)).status_code == 404
