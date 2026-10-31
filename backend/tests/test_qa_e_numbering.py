"""QA fix E4 — numbering settings: only real tokens, a prefix-only change keeps the counter,
and the preview answers on GET as well as POST.

Found in the 5-shop QA run (31 Oct 2026): "TWA-{YYYY}-{C:4}" was saved and issued literally;
changing only the prefix needed a start number (and without one the counter went back to 1);
GET /api/settings/numbering/preview was a 404.
"""
import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize("template", ["TWA-{YYYY}-{C:4}", "X-{C:4}-{CAT}", "X-{c:4}", "X-{C:x}"])
async def test_unknown_tokens_are_refused_by_save_and_preview(client, tenant, template):
    r = await client.put("/api/settings/numbering", json={
        "doc_type": "invoice", "template": template, "begins_from": 10}, headers=tenant)
    assert r.status_code == 422, r.text
    assert "{Y}" in r.json()["detail"]          # names the supported tokens
    p = await client.post("/api/settings/numbering/preview", json={
        "doc_type": "invoice", "template": template}, headers=tenant)
    assert p.status_code == 422, p.text


@pytest.mark.asyncio
async def test_yyyy_hint_points_to_y(client, tenant):
    r = await client.put("/api/settings/numbering", json={
        "doc_type": "invoice", "template": "TWA-{YYYY}-{C:4}"}, headers=tenant)
    assert "{YYYY}" in r.json()["detail"] and "use {Y}" in r.json()["detail"]


@pytest.mark.asyncio
async def test_prefix_only_change_keeps_the_counter(client, tenant, db):
    from backend.services.numbering import next_number
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    r = await client.put("/api/settings/numbering", json={
        "doc_type": "order", "template": "WO-{C:5}", "begins_from": 12}, headers=tenant)
    assert r.status_code == 200, r.text
    r = await client.put("/api/settings/numbering", json={
        "doc_type": "order", "template": "RESPAWN-{C:5}"}, headers=tenant)
    assert r.status_code == 200, r.text
    assert r.json()["example"] == "RESPAWN-00012"
    assert await next_number(db, me["org_id"], "order") == "RESPAWN-00012"


@pytest.mark.asyncio
async def test_prefix_only_on_unconfigured_type_starts_past_existing_docs(client, tenant):
    r = await client.put("/api/settings/numbering", json={
        "doc_type": "transfer", "template": "TR-{C:3}"}, headers=tenant)
    assert r.status_code == 200, r.text
    assert r.json()["example"] == "TR-001"


@pytest.mark.asyncio
async def test_preview_works_on_get(client, tenant):
    r = await client.get("/api/settings/numbering/preview",
                         params={"template": "RR-{Y}-{C:5}", "begins_from": 7}, headers=tenant)
    assert r.status_code == 200, r.text
    ex = r.json()["example"]
    assert ex.startswith("RR-") and ex.endswith("-00007") and "{" not in ex
    bad = await client.get("/api/settings/numbering/preview",
                           params={"template": "RR-{YYYY}-{C:5}"}, headers=tenant)
    assert bad.status_code == 422


@pytest.mark.asyncio
async def test_begins_from_below_one_is_refused(client, tenant):
    r = await client.put("/api/settings/numbering", json={
        "doc_type": "invoice", "template": "INV-{C:5}", "begins_from": 0}, headers=tenant)
    assert r.status_code == 422
