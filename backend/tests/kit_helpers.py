"""Shared setup for the kit (bundle) print tests: a shop with stock, a kit, a job, invoices."""
import uuid

DOC_BODY = (
    "<div id='title'>{Product Title}</div>"
    "<div id='summary'>{Work Summary}</div>"
    "<div id='all'>{All Items Table}</div>"
    "<div id='parts'>{Products Table}</div>"
    "<div id='services'>{Services Table}</div>"
    "<table><tbody><tr><td>{Product Name}</td><td>{Product SKU}</td><td>{Product Qty}</td>"
    "<td>{Product Price}</td><td>{Product Amount}</td></tr></tbody></table>"
    "<div id='total'>{Total Amount}</div><div id='tax'>{Tax Value}</div>"
)


async def loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


async def part(client, h, location, name, sell, cost, qty=10, sku=None):
    pid = (await client.post("/api/inventory/parts", headers=h, json={
        "name": name, "sku": sku or ("SKU-" + uuid.uuid4().hex[:6]),
        "sell_price": sell, "cost_price": cost})).json()["id"]
    if qty:
        await client.post(f"/api/inventory/parts/{pid}/adjust", headers=h,
                          json={"location_id": location, "qty": qty, "movement_type": "receive"})
    return pid


async def service(client, h, name, price):
    return (await client.post("/api/catalog/services", json={"name": name, "price": price}, headers=h)).json()["id"]


async def stock(client, h, pid):
    return (await client.get(f"/api/inventory/parts/{pid}", headers=h)).json()["qty_available"]


async def kit(client, h, comps, name="Screen Replacement", price_mode="sum", price=0, **extra):
    r = await client.post("/api/bundles", headers=h, json={
        "name": name, "price_mode": price_mode, "price": price, "components": comps, **extra})
    assert r.status_code == 201, r.text
    return r.json()


async def job(client, h, location, estimate=False):
    cid = (await client.post("/api/clients", json={"name": "Kit Customer"}, headers=h)).json()["id"]
    r = await client.post("/api/orders", headers=h, json={
        "client_id": cid, "location_id": location, "complaint": "cracked screen",
        "is_estimate": estimate, "lines": []})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"], cid


async def add_kit(client, h, oid, bid, qty=1, **extra):
    r = await client.post(f"/api/orders/{oid}/lines", headers=h, json={
        "kind": "bundle", "line_type": "part", "description": "kit", "ref_id": bid, "qty": qty, **extra})
    assert r.status_code in (200, 201), r.text
    return r.json()


async def add_line(client, h, oid, **body):
    r = await client.post(f"/api/orders/{oid}/lines", headers=h, json=body)
    assert r.status_code in (200, 201), r.text
    return r.json()


async def lines(client, h, oid):
    return (await client.get(f"/api/orders/{oid}", headers=h)).json()["lines"]


async def order(client, h, oid):
    return (await client.get(f"/api/orders/{oid}", headers=h)).json()


async def invoice(client, h, oid, cid, mode="detailed"):
    r = await client.post("/api/finance/invoices", headers=h, json={
        "order_id": oid, "client_id": cid, "display_mode": mode})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def invoice_lines(client, h, iid):
    return (await client.get(f"/api/finance/invoices/{iid}", headers=h)).json()["lines"]


async def job_print(client, h, oid, body=DOC_BODY):
    r = await client.post("/api/documents/print-view", headers=h, json={
        "body_html": body, "doc_type": "work_order", "order_id": oid})
    assert r.status_code == 200, r.text
    return r.json()["html"]


async def track(client, db, oid):
    from sqlalchemy import select

    from backend.models import Order
    token = (await db.execute(select(Order.public_token).where(Order.id == oid))).scalar_one()
    r = await client.get(f"/api/public/track/{token}")
    assert r.status_code == 200, r.text
    return r.json()
