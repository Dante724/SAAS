"""Several items per job, phase 2: collect & invoice some items, item-grouped documents, split an
item into its own job, recommendations per item — and none of it crosses shops or jobs."""
import pytest

from backend.tests.test_multi_asset_jobs import _job, _on, _shop


async def _line(client, h, oid, desc, price, item=None):
    body = {"line_type": "labour", "kind": "custom", "description": desc, "qty": 1, "unit_price": price}
    if item:
        body["order_item_id"] = item
    r = await client.post(f"/api/orders/{oid}/lines", json=body, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["line_id"]


async def _three_items(client, h):
    """A job with the drill (item #1), a saw and a battery, each with work, plus a whole-job fee."""
    oid, cid, _ = await _job(client, h)
    primary = (await client.get(f"/api/orders/{oid}/items", headers=h)).json()["items"][0]["id"]
    saw = (await client.post(f"/api/orders/{oid}/items", json={"label": "Circular saw", "complaint": "blade wobbles"},
                             headers=h)).json()["id"]
    batt = (await client.post(f"/api/orders/{oid}/items", json={"label": "Battery pack"}, headers=h)).json()["id"]
    await _line(client, h, oid, "Replace chuck", 30, primary)
    await _line(client, h, oid, "True blade", 40, saw)
    await _line(client, h, oid, "New blade", 25, saw)
    await _line(client, h, oid, "Cell test", 10, batt)
    await _line(client, h, oid, "Bench fee", 5)
    return oid, {"drill": primary, "saw": saw, "batt": batt}


@pytest.mark.asyncio
async def test_collect_and_invoice_only_those_items(client):
    h = await _shop(client)
    await _on(client, h)
    oid, it = await _three_items(client, h)

    r = await client.post(f"/api/orders/{oid}/items/collect", json={"item_ids": [it["saw"]]}, headers=h)
    assert r.status_code == 201, r.text
    inv = r.json()["invoice"]
    assert inv["total"] == 65 and r.json()["line_count"] == 2
    items = {i["id"]: i for i in r.json()["items"]}
    assert items[it["saw"]]["collected_at"] and not items[it["drill"]]["collected_at"]
    assert items[it["saw"]]["unbilled_lines"] == 0 and items[it["drill"]]["unbilled_lines"] == 1

    # the invoice lists only the saw's lines (not the whole job), under the saw
    detail = (await client.get(f"/api/finance/invoices/{inv['id']}", headers=h)).json()
    assert sorted(ln["description"] for ln in detail["lines"]) == ["New blade", "True blade"]
    assert {ln["item"] for ln in detail["lines"]} == {"Circular saw"}
    # the job is still open
    o = (await client.get(f"/api/orders/{oid}", headers=h)).json()
    assert o["status"] == "new"

    # collecting the saw again bills nothing new
    r = await client.post(f"/api/orders/{oid}/items/collect", json={"item_ids": [it["saw"]]}, headers=h)
    assert r.json()["invoice"] is None

    # the last items out take the whole-job lines with them
    r = await client.post(f"/api/orders/{oid}/items/collect", json={"item_ids": [it["drill"], it["batt"]]}, headers=h)
    inv2 = r.json()["invoice"]
    assert inv2["total"] == 45
    d2 = (await client.get(f"/api/finance/invoices/{inv2['id']}", headers=h)).json()
    # item by item in the job's item order, whole-job last
    assert [(ln["item"], ln["description"]) for ln in d2["lines"]] == [
        ("DeWalt DCD791", "Replace chuck"), ("Battery pack", "Cell test"), (None, "Bench fee")]


@pytest.mark.asyncio
async def test_collect_needs_the_setting_and_invoice_permission(client):
    h = await _shop(client)
    oid, _, _ = await _job(client, h)
    # setting off → refused
    assert (await client.post(f"/api/orders/{oid}/items/collect", json={"item_ids": ["x"]}, headers=h)).status_code == 409
    await _on(client, h)
    assert (await client.post(f"/api/orders/{oid}/items/collect", json={"item_ids": []}, headers=h)).status_code == 422
    assert (await client.post(f"/api/orders/{oid}/items/collect", json={"item_ids": ["nope"]}, headers=h)).status_code == 422


@pytest.mark.asyncio
async def test_close_drafts_only_what_was_not_billed(client):
    h = await _shop(client)
    await _on(client, h)
    await client.put("/api/staff/company", json={"auto_draft_invoice_on_close": True}, headers=h)
    oid, it = await _three_items(client, h)
    await client.post(f"/api/orders/{oid}/items/collect", json={"item_ids": [it["saw"]]}, headers=h)
    r = await client.patch(f"/api/orders/{oid}/status", json={"status": "done"}, headers=h)
    assert r.status_code == 200, r.text
    invs = (await client.get(f"/api/cash/orders/{oid}/invoices", headers=h)).json()["invoices"]
    assert sorted(i["total"] for i in invs) == [45, 65]      # 110 total, billed once


@pytest.mark.asyncio
async def test_documents_group_lines_by_item(client):
    h = await _shop(client)
    await _on(client, h)
    oid, it = await _three_items(client, h)
    r = await client.post("/api/documents/print-view", json={"body_html": "{All Items Table}", "doc_type": "work_order",
                                                             "order_id": oid}, headers=h)
    assert r.status_code == 200, r.text
    html = r.json()["html"]
    i_drill, i_saw, i_batt, i_job = (html.index(x) for x in ("DeWalt DCD791", "Circular saw", "Battery pack", "Whole job"))
    assert i_drill < i_saw < i_batt < i_job
    assert "Circular saw subtotal" in html and html.index("Bench fee") > i_job

    inv = (await client.post(f"/api/orders/{oid}/items/collect", json={"item_ids": [it["saw"], it["drill"]],
                                                                       "include_job_lines": True}, headers=h)).json()["invoice"]
    pdf = await client.get(f"/api/print/invoices/{inv['id']}", headers=h)
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"


@pytest.mark.asyncio
async def test_split_item_into_its_own_job(client):
    h = await _shop(client)
    await _on(client, h)
    oid, it = await _three_items(client, h)
    src = (await client.get(f"/api/orders/{oid}", headers=h)).json()

    assert (await client.post(f"/api/orders/{oid}/items/{it['drill']}/split", headers=h)).status_code == 409
    r = await client.post(f"/api/orders/{oid}/items/{it['saw']}/split", headers=h)
    assert r.status_code == 201, r.text
    new_id = r.json()["id"]
    assert r.json()["line_count"] == 2 and r.json()["split_off"] == [{"id": new_id, "order_number": r.json()["order_number"]}]

    old = (await client.get(f"/api/orders/{oid}", headers=h)).json()
    new = (await client.get(f"/api/orders/{new_id}", headers=h)).json()
    assert sorted(ln["description"] for ln in old["lines"]) == ["Bench fee", "Cell test", "Replace chuck"]
    assert old["total"] == 45 and new["total"] == 65
    assert sorted(ln["description"] for ln in new["lines"]) == ["New blade", "True blade"]
    assert all(ln["order_item_id"] == it["saw"] for ln in new["lines"])
    assert new["client"]["id"] == src["client"]["id"] and new["complaint"] == "blade wobbles"
    assert new["public_token"] != src["public_token"]
    moved = (await client.get(f"/api/orders/{new_id}/items", headers=h)).json()
    assert [i["id"] for i in moved["items"]] == [it["saw"]] and moved["split_from"]["id"] == oid
    assert it["saw"] not in [i["id"] for i in (await client.get(f"/api/orders/{oid}/items", headers=h)).json()["items"]]
    # the new job's only item can't be split again (it would leave an empty job)
    assert (await client.post(f"/api/orders/{new_id}/items/{it['saw']}/split", headers=h)).status_code == 409


@pytest.mark.asyncio
async def test_cant_split_billed_or_collected_items(client):
    h = await _shop(client)
    await _on(client, h)
    oid, it = await _three_items(client, h)
    await client.post(f"/api/orders/{oid}/items/collect", json={"item_ids": [it["saw"]]}, headers=h)
    assert (await client.post(f"/api/orders/{oid}/items/{it['saw']}/split", headers=h)).status_code == 409
    # billed by a per-line sub-invoice, not collected
    batt_line = next(ln["id"] for ln in (await client.get(f"/api/orders/{oid}", headers=h)).json()["lines"]
                     if ln["description"] == "Cell test")
    assert (await client.post(f"/api/cash/orders/{oid}/invoices", json={"line_ids": [batt_line]}, headers=h)).status_code == 201
    assert (await client.post(f"/api/orders/{oid}/items/{it['batt']}/split", headers=h)).status_code == 409


@pytest.mark.asyncio
async def test_recommendations_per_item(client):
    h = await _shop(client)
    await _on(client, h)
    oid, it = await _three_items(client, h)
    token = (await client.get(f"/api/orders/{oid}", headers=h)).json()["public_token"]
    r = await client.post(f"/api/orders/{oid}/recommendations", json={"title": "New carbon brushes", "kind": "custom",
                                                                      "unit_price": 18, "order_item_id": it["saw"]}, headers=h)
    assert r.status_code == 201, r.text
    rec = r.json()
    assert rec["order_item_id"] == it["saw"]
    whole = (await client.post(f"/api/orders/{oid}/recommendations", json={"title": "Clean all", "unit_price": 5},
                               headers=h)).json()
    await client.post(f"/api/orders/{oid}/recommendations/publish-all", json={}, headers=h)

    track = (await client.get(f"/api/public/track/{token}")).json()
    by = {x["title"]: x for x in track["recommendations"]}
    assert by["New carbon brushes"]["item"] == "Circular saw" and by["Clean all"]["item"] is None
    assert {ln["label"]: ln["item"] for ln in track["lines"]}["True blade"] == "Circular saw"

    r = await client.post(f"/api/public/track/{token}/recommendations/decide",
                          json={"approve_ids": [rec["id"]], "decline_ids": [whole["id"]]})
    assert r.status_code == 200 and r.json()["approved"] == 1
    line = next(ln for ln in (await client.get(f"/api/orders/{oid}", headers=h)).json()["lines"]
                if ln["description"] == "New carbon brushes")
    assert line["order_item_id"] == it["saw"]

    # re-tag / untag; another job's item is refused
    other, _, _ = await _job(client, h)
    other_item = (await client.get(f"/api/orders/{other}/items", headers=h)).json()["items"][0]["id"]
    r2 = (await client.post(f"/api/orders/{oid}/recommendations", json={"title": "Grease"}, headers=h)).json()
    assert (await client.patch(f"/api/orders/{oid}/recommendations/{r2['id']}", json={"order_item_id": other_item},
                               headers=h)).status_code == 422
    assert (await client.patch(f"/api/orders/{oid}/recommendations/{r2['id']}", json={"order_item_id": it["batt"]},
                               headers=h)).json()["order_item_id"] == it["batt"]
    # removing the item un-tags its recommendations
    await client.delete(f"/api/orders/{oid}/items/{it['batt']}", headers=h)
    recs = {x["id"]: x for x in (await client.get(f"/api/orders/{oid}/recommendations", headers=h)).json()["items"]}
    assert recs[r2["id"]]["order_item_id"] is None


@pytest.mark.asyncio
async def test_split_takes_the_items_recommendations(client):
    h = await _shop(client)
    await _on(client, h)
    oid, it = await _three_items(client, h)
    rec = (await client.post(f"/api/orders/{oid}/recommendations", json={"title": "Brushes", "order_item_id": it["saw"]},
                             headers=h)).json()
    new_id = (await client.post(f"/api/orders/{oid}/items/{it['saw']}/split", headers=h)).json()["id"]
    assert [x["id"] for x in (await client.get(f"/api/orders/{new_id}/recommendations", headers=h)).json()["items"]] == [rec["id"]]
    assert (await client.get(f"/api/orders/{oid}/recommendations", headers=h)).json()["items"] == []


@pytest.mark.asyncio
async def test_phase2_nothing_crosses_shops(client):
    a = await _shop(client)
    b = await _shop(client)
    await _on(client, a)
    await _on(client, b)
    a_job, a_items = await _three_items(client, a)
    b_job, b_items = await _three_items(client, b)

    # B can't collect, split or tag recommendations on A's job, or use A's items on its own job
    assert (await client.post(f"/api/orders/{a_job}/items/collect", json={"item_ids": [a_items["saw"]]}, headers=b)).status_code == 404
    assert (await client.post(f"/api/orders/{a_job}/items/{a_items['saw']}/split", headers=b)).status_code == 404
    assert (await client.post(f"/api/orders/{b_job}/items/collect", json={"item_ids": [a_items["saw"]]}, headers=b)).status_code == 422
    assert (await client.post(f"/api/orders/{b_job}/items/{a_items['saw']}/split", headers=b)).status_code == 422
    assert (await client.post(f"/api/orders/{b_job}/recommendations", json={"title": "x", "order_item_id": a_items["saw"]},
                              headers=b)).status_code == 422
    # A's split job is invisible to B, and A's job doesn't list B's work
    new_a = (await client.post(f"/api/orders/{a_job}/items/{a_items['saw']}/split", headers=a)).json()["id"]
    assert (await client.get(f"/api/orders/{new_a}/items", headers=b)).status_code == 404
    assert (await client.get(f"/api/orders/{b_job}/items", headers=b)).json()["split_off"] == []
    # each shop's collect bills only its own lines
    inv_b = (await client.post(f"/api/orders/{b_job}/items/collect", json={"item_ids": [b_items["saw"]]}, headers=b)).json()["invoice"]
    assert inv_b["total"] == 65
    assert (await client.get(f"/api/finance/invoices/{inv_b['id']}", headers=a)).status_code == 404
