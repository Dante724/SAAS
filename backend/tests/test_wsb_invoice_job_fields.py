"""Workstream B — job fields on invoices (gap #7): job number, fault, technician notes and job
custom fields as invoice template variables, and per-row job data on an invoice billing several
jobs. Values are escaped; rendering needs the invoices permission."""
import uuid

import pytest
from sqlalchemy import select

pytestmark = pytest.mark.asyncio

BODY = ("<p>Job {Order Number} · {Order Malfunction} · {Order Complaint} · {Order Technician Notes}"
        " · {Order Colour Code}</p>"
        "<table><tbody><tr><td>{Row Order Number}</td><td>{Row Order Fault}</td><td>{Product Name}</td></tr>"
        "</tbody></table>")


async def _job(client, H, complaint, line):
    o = (await client.post("/api/orders", json={"complaint": complaint}, headers=H)).json()
    await client.post(f"/api/orders/{o['id']}/lines", json={"line_type": "labour", "kind": "service",
                                                            "description": line, "qty": 1, "unit_price": 10},
                      headers=H)
    return o


async def _tpl(client, H):
    return (await client.post("/api/documents/templates", json={"name": "Inv", "doc_type": "invoice",
                                                                "body_html": BODY}, headers=H)).json()["id"]


async def test_job_fields_resolve_on_an_invoice(client, tenant, db):
    from backend.models import CustomField, Order
    o = await _job(client, tenant, "Screen <b>cracked</b>", "Screen swap")
    row = (await db.execute(select(Order).where(Order.id == o["id"]))).scalar_one()
    row.tech_notes = "Replaced digitizer"
    row.custom_data = {"colour_code": "RED"}
    db.add(CustomField(org_id=row.org_id, entity_type="order", field_key="colour_code",
                       field_label="Colour Code", field_type="text"))
    await db.commit()
    iid = (await client.post("/api/finance/invoices", json={"order_id": o["id"], "subtotal": 10},
                             headers=tenant)).json()["id"]
    html = (await client.get(f"/api/documents/render-invoice/{iid}/{await _tpl(client, tenant)}",
                             headers=tenant)).json()["html"]
    assert o["order_number"] in html
    assert "Replaced digitizer" in html and "RED" in html
    assert "<b>cracked</b>" not in html and "&lt;b&gt;cracked&lt;/b&gt;" in html
    assert "{Order" not in html and "{Row Order" not in html


async def test_grouped_invoice_rows_carry_their_own_job(client, tenant):
    a = await _job(client, tenant, "No power", "Board repair")
    b = await _job(client, tenant, "Water damage", "Cleaning")
    iid = (await client.post("/api/finance/invoices", json={"order_ids": [a["id"], b["id"]]},
                             headers=tenant)).json()["id"]
    html = (await client.get(f"/api/documents/render-invoice/{iid}/{await _tpl(client, tenant)}",
                             headers=tenant)).json()["html"]
    import re
    rows = re.findall(r"<tr><td>([^<]*)</td><td>([^<]*)</td><td>([^<]*)</td></tr>", html)
    assert (a["order_number"], "No power", "Board repair") in rows
    assert (b["order_number"], "Water damage", "Cleaning") in rows


async def test_new_variables_are_in_the_editor_catalog(client, tenant):
    d = (await client.get("/api/documents/templates", headers=tenant)).json()
    inv = next(g for g in d["variable_groups"] if g["group"] == "Invoice")
    for v in ("{Order Number}", "{Order Malfunction}", "{Order Technician Notes}", "{Row Order Number}",
              "{Row Order Fault}"):
        assert v in inv["items"], v


async def test_rendering_an_invoice_needs_the_invoices_permission(client, tenant):
    o = await _job(client, tenant, "Fault", "Line")
    iid = (await client.post("/api/finance/invoices", json={"order_id": o["id"], "subtotal": 10},
                             headers=tenant)).json()["id"]
    tpl = await _tpl(client, tenant)
    role = (await client.post("/api/staff/roles", json={"name": "NoInv", "permissions": {"can_view_invoices": False}},
                              headers=tenant)).json()
    em = f"n-{uuid.uuid4().hex[:6]}@t.dev"
    await client.post("/api/staff/employees", json={"name": "N", "email": em, "password": "pw-test-12345",
                                                     "role_id": role["id"]}, headers=tenant)
    tok = (await client.post("/api/auth/token", data={"username": em, "password": "pw-test-12345"})).json()
    H = {"Authorization": f"Bearer {tok['access_token']}"}
    assert (await client.get(f"/api/documents/render-invoice/{iid}/{tpl}", headers=H)).status_code == 403
