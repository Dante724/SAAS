"""Launch fixes, batch 2: purchase orders can't be over-received (B21), document templates are
cleaned on the server (H5), and GDPR erase/export cover photos, signatures, calls and
inquiries (P9)."""
import pytest
from sqlalchemy import select

from backend.models import (
    Call,
    Client,
    DocumentSignature,
    EventFeedItem,
    Lead,
    MediaBlob,
    Order,
    POLine,
    PurchaseOrder,
    Supplier,
)
from backend.services.html_clean import clean_template_html


@pytest.mark.asyncio
async def test_po_line_cannot_be_received_beyond_what_was_ordered(client, tenant, db):
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    pid = (await client.post("/api/inventory/parts", json={"name": "Belt", "sku": "BELT-9"}, headers=tenant)).json()["id"]
    sup = Supplier(org_id=me["org_id"], name="S", balance=0)
    db.add(sup)
    await db.flush()
    po = PurchaseOrder(supplier_id=sup.id, location_id=loc, po_number="PO-B21", status="ordered")
    db.add(po)
    await db.flush()
    line = POLine(po_id=po.id, part_id=pid, qty_ordered=10, unit_cost=5)
    db.add(line)
    await db.commit()
    url = f"/api/inventory/po/{po.id}/receive"
    assert (await client.post(url, headers=tenant, json={"lines": [{"po_line_id": line.id, "qty": 6}]})).status_code == 200
    r = await client.post(url, headers=tenant, json={"lines": [{"po_line_id": line.id, "qty": 5}]})
    assert r.status_code == 409 and "only 4" in r.text, r.text
    assert (await client.post(url, headers=tenant, json={"lines": [{"po_line_id": line.id, "qty": 4}]})).status_code == 200
    await db.refresh(line)
    assert line.qty_received == 10


def test_template_cleaner_keeps_layout_and_markers_but_drops_script():
    tpl = ('<style>.t{color:red}</style><table><tbody>{#if paid}<tr><td style="width:40mm">{{client_name}}'
           '</td></tr>{/if}</tbody></table><img src="{{logo_url}}" onerror="x()">'
           '<p onclick="steal()">Hi</p><script>fetch("/api/x")</script><a href="javascript:alert(1)">x</a>')
    out = clean_template_html(tpl)
    assert "<script" not in out and "onerror" not in out and "onclick" not in out and "javascript:" not in out
    assert out.startswith("<style>.t{color:red}</style><table><tbody>{#if paid}<tr>")
    assert "{{client_name}}" in out and "{/if}</tbody>" in out and 'src="{{logo_url}}"' in out


@pytest.mark.asyncio
async def test_saved_and_rendered_templates_are_cleaned(client, tenant):
    r = await client.post("/api/documents/templates", headers=tenant, json={
        "name": "Bad", "doc_type": "estimate", "body_html": "<p>{{client_name}}</p><script>alert(1)</script>"})
    assert r.status_code == 201, r.text
    tpls = (await client.get("/api/documents/templates", headers=tenant)).json()
    items = tpls["items"] if isinstance(tpls, dict) else tpls
    saved = next(t for t in items if t["id"] == r.json()["id"])
    assert "<script" not in saved["body_html"] and "{{client_name}}" in saved["body_html"]


@pytest.mark.asyncio
async def test_erase_destroys_photos_and_signatures_and_export_has_calls_and_inquiries(client, tenant, db):
    cid = (await client.post("/api/clients", headers=tenant, json={"name": "Erin", "phone": "+15550199"})).json()["id"]
    oid = (await client.post("/api/orders", headers=tenant, json={"client_id": cid, "complaint": "x"})).json()["id"]
    cl = (await db.execute(select(Client).where(Client.id == cid))).scalar_one()
    org_id = cl.org_id
    photo = MediaBlob(org_id=org_id, backend="db", inline_data="data:image/png;base64,AAAA", mime="image/png")
    sig = MediaBlob(org_id=org_id, backend="db", inline_data="data:image/png;base64,BBBB", mime="image/png")
    feed_blob = MediaBlob(org_id=org_id, backend="db", inline_data="data:image/png;base64,CCCC", mime="image/png")
    db.add_all([photo, sig, feed_blob])
    await db.flush()
    order = (await db.execute(select(Order).where(Order.id == oid))).scalar_one()
    order.intake_photos, order.signature_blob_id = [photo.id], sig.id
    db.add(EventFeedItem(org_id=org_id, entity_type="order", entity_id=oid, item_type="photo",
                         media_blob_id=feed_blob.id, media_data="data:image/png;base64,CCCC", body="cracked"))
    db.add(DocumentSignature(org_id=org_id, order_id=oid, signer_name="Erin", signature_png="data:image/png;base64,DD",
                             signer_ip="1.2.3.4", doc_hash="h" * 64))
    db.add(Call(org_id=org_id, client_id=cid, from_number="+15550199", transcript="hello"))
    db.add(Lead(org_id=org_id, client_id=cid, name="Erin", phone="+15550199", inquiry="screen"))
    await db.commit()

    exp = (await client.get(f"/api/compliance/gdpr/client/{cid}/export", headers=tenant)).json()
    assert exp["calls"][0]["transcript"] == "hello" and exp["inquiries"][0]["inquiry"] == "screen"

    blob_ids = [photo.id, sig.id, feed_blob.id]
    r = await client.post(f"/api/compliance/gdpr/client/{cid}/erase", headers=tenant)
    assert r.status_code == 200, r.text
    db.expire_all()
    order = (await db.execute(select(Order).where(Order.id == oid))).scalar_one()
    assert order.intake_photos == [] and order.signature_blob_id is None
    for b in (await db.execute(select(MediaBlob).where(MediaBlob.id.in_(blob_ids)))).scalars():
        assert b.inline_data is None
    item = (await db.execute(select(EventFeedItem).where(EventFeedItem.entity_id == oid,
                                                         EventFeedItem.item_type == "photo"))).scalar_one()
    assert item.media_data is None and item.media_blob_id is None
    s = (await db.execute(select(DocumentSignature).where(DocumentSignature.order_id == oid))).scalar_one()
    assert s.signature_png is None and s.signer_name is None and s.signer_ip is None and s.doc_hash == "h" * 64
