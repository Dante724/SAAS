"""Tekly Go tablet intake and technician tools (gc144g3).

- a promised-by date set at intake lands on the job (due_at drives "overdue")
- photos can be labelled check-in / before / after / damage, on upload or afterwards, and a
  label goes away with its photo
- the drop-off terms the customer signs: built-in wording until the shop writes its own
"""
import pytest

PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="


@pytest.mark.asyncio
async def test_due_date_set_at_intake(client, tenant):
    r = await client.post("/api/orders", headers=tenant, json={"complaint": "screen", "priority": 1, "due_at": "2030-05-01T17:00:00"})
    assert r.status_code == 201, r.text
    o = (await client.get(f"/api/orders/{r.json()['id']}", headers=tenant)).json()
    assert o["due_at"].startswith("2030-05-01") and o["priority"] == 1


@pytest.mark.asyncio
async def test_bad_due_date_is_refused(client, tenant):
    r = await client.post("/api/orders", headers=tenant, json={"complaint": "x", "due_at": "next tuesday"})
    assert r.status_code in (400, 422)


@pytest.mark.asyncio
async def test_photo_labels(client, tenant):
    a = (await client.post("/api/media", headers=tenant, json={"data": PNG})).json()["id"]
    b = (await client.post("/api/media", headers=tenant, json={"data": PNG})).json()["id"]
    oid = (await client.post("/api/orders", headers=tenant, json={"complaint": "pics", "intake_photos": [a]})).json()["id"]

    # a label given on upload applies only to the photos that call added
    r = await client.post(f"/api/orders/{oid}/intake-photos/add", headers=tenant, json={"photos": [a, b], "label": "after"})
    labels = {p["id"]: p["label"] for p in r.json()["intake_photos"]}
    assert labels == {a: None, b: "after"}

    # relabel and clear
    r = await client.patch(f"/api/orders/{oid}/intake-photos/{a}/label", headers=tenant, json={"label": "before"})
    assert r.status_code == 200
    assert {p["id"]: p["label"] for p in r.json()["intake_photos"]} == {a: "before", b: "after"}
    r = await client.patch(f"/api/orders/{oid}/intake-photos/{b}/label", headers=tenant, json={"label": None})
    assert {p["id"]: p["label"] for p in r.json()["intake_photos"]}[b] is None

    # only known labels, only photos on this job
    assert (await client.patch(f"/api/orders/{oid}/intake-photos/{a}/label", headers=tenant, json={"label": "selfie"})).status_code == 422
    assert (await client.patch(f"/api/orders/{oid}/intake-photos/nope/label", headers=tenant, json={"label": "after"})).status_code == 404

    # removing a photo drops its label
    await client.patch(f"/api/orders/{oid}/intake-photos/{a}/label", headers=tenant, json={"label": "damage"})
    r = await client.patch(f"/api/orders/{oid}/intake-photos", headers=tenant, json={"photos": [b]})
    assert [p["id"] for p in r.json()["intake_photos"]] == [b]
    r = await client.post(f"/api/orders/{oid}/intake-photos/add", headers=tenant, json={"photos": [a]})
    assert {p["id"]: p["label"] for p in r.json()["intake_photos"]}[a] is None


@pytest.mark.asyncio
async def test_dropoff_terms_default_then_custom(client, tenant):
    r = (await client.get("/api/mobile/dropoff-terms", headers=tenant)).json()
    assert r["custom"] is False and "data" in r["terms"].lower()
    assert (await client.put("/api/staff/public-pages", headers=tenant, json={"dropoff_terms": "Our terms: be nice."})).status_code == 200
    r = (await client.get("/api/mobile/dropoff-terms", headers=tenant)).json()
    assert r == {**r, "custom": True, "terms": "Our terms: be nice."}
    assert (await client.get("/api/staff/public-pages", headers=tenant)).json()["dropoff_terms"] == "Our terms: be nice."


@pytest.mark.asyncio
async def test_part_request_becomes_an_urgent_task_on_the_job(client, db, tenant):
    from backend.tests.test_sensitive_reads import _restricted
    oid = (await client.post("/api/orders", headers=tenant, json={"complaint": "no charge"})).json()["id"]
    num = (await client.get(f"/api/orders/{oid}", headers=tenant)).json()["order_number"]
    # a technician who can't hand tasks to others can still ask for a part
    h, _, _ = await _restricted(client, db, ["can_assign_staff"])
    r = await client.post("/api/tasks/part-request", headers={**h, "Idempotency-Key": "pr-1"},
                          json={"order_id": oid, "description": "USB-C port", "qty": 2, "note": "Pins bent"})
    assert r.status_code == 201, r.text
    t = r.json()
    assert t["title"] == f"Order part: USB-C port ×2 for {num}"
    assert t["order_id"] == oid and t["priority"] == 1 and t["description"] == "Pins bent"
    # a retried tap makes one task
    r2 = await client.post("/api/tasks/part-request", headers={**h, "Idempotency-Key": "pr-1"},
                           json={"order_id": oid, "description": "USB-C port", "qty": 2, "note": "Pins bent"})
    assert r2.json()["id"] == t["id"]
    assert (await client.post("/api/tasks/part-request", headers=tenant, json={"order_id": "nope", "description": "x"})).status_code == 404
    assert (await client.post("/api/tasks/part-request", headers=tenant, json={"order_id": oid, "description": ""})).status_code == 422
