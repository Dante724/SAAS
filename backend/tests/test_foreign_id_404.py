"""ISO-1 follow-up: id-routes answer 404 for another shop's id.

The isolation sweep (tests-e2e/api_gate/isolation.py) found 21 routes that answered 200 when
shop B used shop A's id: GETs that came back as an empty list, writes that did nothing, and
DELETEs that said {"ok": true} without deleting anything. No data leaked, but every other route
says 404 for a foreign id, and these now do too.

For each route: shop B calls it with shop A's real id and gets 404; A's record reads back
exactly as before; then A calls the same route with the same id and it still works.
"""
import uuid

import pytest
from sqlalchemy import func, select

from backend.models import GeneratedDocument


async def _register(client, name):
    email = f"fid-{uuid.uuid4().hex[:8]}@test.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": name, "name": f"Owner {name}", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _ok(r, code=(200, 201)):
    assert r.status_code in ((code,) if isinstance(code, int) else code), r.text
    return r.json()


async def _seed(client, h):
    """One record of every kind these routes take, in shop A, with some children under each."""
    ids = {"employee": (await _ok(await client.get("/api/auth/me", headers=h)))["id"]}
    cid = (await _ok(await client.post("/api/clients", json={"name": "Shop A client"}, headers=h)))["id"]
    ids["client"] = cid
    await _ok(await client.post(f"/api/compliance/gdpr/client/{cid}/consent",
                                json={"channel": "marketing", "granted": True}, headers=h))
    await _ok(await client.post(f"/api/clients/{cid}/coupons",
                                json={"kind": "pct", "value": 10}, headers=h))
    ids["order"] = (await _ok(await client.post("/api/orders", json={"client_id": cid, "complaint": "x"},
                                                headers=h)))["id"]
    ids["part"] = (await _ok(await client.post("/api/inventory/parts",
                                               json={"name": "A part", "sku": f"A-{uuid.uuid4().hex[:6]}"},
                                               headers=h)))["id"]
    ids["service"] = (await _ok(await client.post("/api/catalog/services", json={"name": "A service"},
                                                  headers=h)))["id"]
    await _ok(await client.post(f"/api/catalog/services/{ids['service']}/commissions",
                                json={"calc_type": "fixed", "amount": 5}, headers=h))
    pt = (await _ok(await client.post("/api/pricing/types", json={"name": "A trade", "markup_pct": 20},
                                      headers=h)))["id"]
    ids["price_type"] = pt
    await _ok(await client.post(f"/api/pricing/types/{pt}/tiers", json={"min_qty": 10, "markup_pct": 15},
                                headers=h))
    await _ok(await client.put(f"/api/pricing/types/{pt}/items",
                               json={"item_type": "product", "item_id": ids["part"], "price": 9},
                               headers=h))
    wt = (await _ok(await client.post("/api/forms/types", json={"name": "A type"}, headers=h)))["id"]
    ids["wo_type"] = wt
    ids["fields"] = [(await _ok(await client.post(f"/api/forms/types/{wt}/fields",
                                                  json={"field_label": f"F{i}", "field_type": "text"},
                                                  headers=h)))["id"] for i in range(3)]
    d = (await _ok(await client.post("/api/directories", json={"key": "a_list", "name": "A list"},
                                     headers=h)))["id"]
    ids["directory"] = d
    ids["dir_items"] = [(await _ok(await client.post(f"/api/directories/{d}/items", json={"value": v},
                                                     headers=h)))["id"] for v in ("one", "two", "three")]
    ids["doc_template"] = (await _ok(await client.post("/api/documents/templates", json={
        "name": "A auto", "doc_type": "work_order", "body_html": "<p>{{order_number}}</p>",
        "auto_print_events": ["create"]}, headers=h)))["id"]
    ids["bundle"] = (await _ok(await client.post("/api/bundles", json={
        "name": "A kit", "components": [{"ref_type": "product", "ref_id": ids["part"], "qty": 1}]},
        headers=h)))["id"]
    ids["label_template"] = (await _ok(await client.post("/api/print/templates", json={
        "name": "A label", "fields": ["name"]}, headers=h)))["id"]
    ids["resource"] = (await _ok(await client.post("/api/resources", json={"name": "A bay"}, headers=h)))["id"]
    return ids


async def _generated_docs(db, ids):
    return (await db.execute(select(func.count()).select_from(GeneratedDocument).where(
        GeneratedDocument.order_id == ids["order"]))).scalar()


def _live(key, rows_path):
    """Snapshot reader for a soft/hard delete: is this record still listed (and active) for A?"""
    async def read(client, h, db, ids):
        body = (await _ok(await client.get(rows_path, headers=h)))
        rows = body.get("items", body) if isinstance(body, dict) else body
        return sorted((r["id"], r.get("active", True)) for r in rows if r["id"] == ids[key])
    return read


def _get(path):
    async def read(client, h, db, ids):
        r = await client.get(path.format(**ids), headers=h)
        assert r.status_code == 200, r.text
        return r.text
    return read


# (id, method, path, body(ids) or None, snapshot reader for A's record)
CASES = [
    ("client_calls", "GET", "/api/clients/{client}/calls", None, None),
    ("client_coupons", "GET", "/api/clients/{client}/coupons", None, None),
    ("client_documents", "GET", "/api/clients/{client}/documents", None, None),
    ("client_feedback", "GET", "/api/clients/{client}/feedback", None, None),
    ("gdpr_consent", "GET", "/api/compliance/gdpr/client/{client}/consent", None, None),
    ("bin_part_detail", "GET", "/api/bins/part/{part}/detail", None, None),
    ("service_commissions", "GET", "/api/catalog/services/{service}/commissions", None, None),
    ("part_purchase_orders", "GET", "/api/inventory/parts/{part}/purchase-orders", None, None),
    ("payroll_balance", "GET", "/api/payroll/balance/{employee}", None, None),
    ("price_type_items", "GET", "/api/pricing/types/{price_type}/items", None, None),
    ("price_type_tiers", "GET", "/api/pricing/types/{price_type}/tiers", None, None),
    ("form_type_fields", "GET", "/api/forms/types/{wo_type}/fields", None, None),
    ("directory_export", "GET", "/api/directories/{directory}/export", None, None),
    ("directory_reorder", "POST", "/api/directories/{directory}/reorder",
     lambda ids: {"ids": list(reversed(ids["dir_items"]))}, _get("/api/directories/{directory}/items")),
    ("form_fields_reorder", "PATCH", "/api/forms/types/{wo_type}/fields/reorder",
     lambda ids: {"ordered_ids": list(reversed(ids["fields"]))}, _get("/api/forms/types/{wo_type}/fields")),
    ("auto_print", "POST", "/api/documents/auto-print/{order}?event=create", None,
     lambda client, h, db, ids: _generated_docs(db, ids)),
    ("delete_bundle", "DELETE", "/api/bundles/{bundle}", None, _live("bundle", "/api/bundles")),
    ("delete_doc_template", "DELETE", "/api/documents/templates/{doc_template}", None,
     _live("doc_template", "/api/documents/templates")),
    ("delete_price_type", "DELETE", "/api/pricing/types/{price_type}", None,
     _live("price_type", "/api/pricing/types")),
    ("delete_label_template", "DELETE", "/api/print/templates/{label_template}", None,
     _live("label_template", "/api/print/templates")),
    ("delete_resource", "DELETE", "/api/resources/{resource}", None, _live("resource", "/api/resources")),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("name,method,path,body,snapshot", CASES, ids=[c[0] for c in CASES])
async def test_foreign_id_is_404_and_own_id_still_works(client, db, name, method, path, body, snapshot):
    ha = await _register(client, "Shop A")
    hb = await _register(client, "Shop B")
    ids = await _seed(client, ha)
    url = path.format(**ids)
    payload = body(ids) if body else None
    read = snapshot or _get(path)            # a GET route is its own snapshot
    before = await read(client, ha, db, ids)
    assert before not in (None, [], ""), f"{name}: A's record should be there before the probe"

    r = await client.request(method, url, headers=hb, json=payload)
    assert r.status_code == 404, f"{name}: shop B got {r.status_code} for shop A's id: {r.text}"

    assert await read(client, ha, db, ids) == before, f"{name}: shop B's call changed A's record"

    # Same shop, same id: still works, same shape as before.
    own = await client.request(method, url, headers=ha, json=payload)
    assert own.status_code == 200, f"{name}: shop A's own call failed: {own.text}"
    if method == "DELETE":
        assert own.json() == {"ok": True}


@pytest.mark.asyncio
async def test_set_price_item_on_foreign_type_is_404_and_stores_nothing(client, db):
    """PUT /api/pricing/types/{id}/items with another shop's price type and a valid body: 404 from
    the price-type lookup, and no price row is written in either shop."""
    from backend.models import PriceTypeItem
    ha = await _register(client, "Shop A")
    hb = await _register(client, "Shop B")
    ids = await _seed(client, ha)
    b_part = (await _ok(await client.post("/api/inventory/parts",
                                          json={"name": "B part", "sku": f"B-{uuid.uuid4().hex[:6]}"},
                                          headers=hb)))["id"]
    url = f"/api/pricing/types/{ids['price_type']}/items"
    before = await _ok(await client.get(url, headers=ha))

    r = await client.put(url, json={"item_type": "product", "item_id": b_part, "price": 7}, headers=hb)
    assert r.status_code == 404, r.text
    # The shop-scoped lookup answers, before anything is written (not a guard tripping on flush).
    detail = r.json()["detail"]
    assert isinstance(detail, dict) and detail.get("message") == \
        "That price type doesn't exist any more. Pick another.", r.text

    # Nothing stored in either shop: A's type still has only its seeded row, and B's part has no price.
    on_type = (await db.execute(select(PriceTypeItem.item_id).where(
        PriceTypeItem.price_type_id == ids["price_type"]))).scalars().all()
    assert on_type == [ids["part"]]
    assert (await db.execute(select(func.count()).select_from(PriceTypeItem).where(
        PriceTypeItem.item_id == b_part))).scalar() == 0
    assert await _ok(await client.get(url, headers=ha)) == before

    # Same shop, same type: still saves.
    own = await client.put(url, json={"item_type": "product", "item_id": ids["part"], "price": 11}, headers=ha)
    assert own.status_code == 200 and own.json() == {"ok": True}, own.text
