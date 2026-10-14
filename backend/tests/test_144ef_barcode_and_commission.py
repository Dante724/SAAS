"""Build 144ef — app-wide barcode/QR generator endpoint + service exceptional
commissions (Orderry parity)."""
import uuid
import pytest

from backend.services.payroll import CommissionEngine


async def _register(client):
    email = f"a-{uuid.uuid4().hex[:8]}@t.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Co", "name": "Admin", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.mark.asyncio
async def test_code_svg_renders_code128_and_qr(client):
    H = await _register(client)
    for t in ("code128", "qr"):
        r = await client.get(f"/api/catalog/code.svg?type={t}&data=WO-0001", headers=H)
        assert r.status_code == 200, r.text
        assert "image/svg+xml" in r.headers["content-type"]
        assert r.text.startswith("<svg")


@pytest.mark.asyncio
async def test_code_svg_requires_data(client):
    H = await _register(client)
    r = await client.get("/api/catalog/code.svg?type=code128&data=", headers=H)
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_service_commission_crud(client):
    H = await _register(client)
    s = (await client.post("/api/catalog/services", json={"name": "Diagnostics"}, headers=H)).json()
    # add a service-wide 15% rule
    r = await client.post(f"/api/catalog/services/{s['id']}/commissions",
                          json={"calc_type": "percent", "amount": 15}, headers=H)
    assert r.status_code == 201, r.text
    rule = r.json()
    assert rule["amount"] == 15 and rule["calc_type"] == "percent"
    lst = (await client.get(f"/api/catalog/services/{s['id']}/commissions", headers=H)).json()
    assert len(lst["items"]) == 1
    # delete it
    d = await client.delete(f"/api/catalog/services/{s['id']}/commissions/{rule['id']}", headers=H)
    assert d.status_code == 200
    lst2 = (await client.get(f"/api/catalog/services/{s['id']}/commissions", headers=H)).json()
    assert lst2["items"] == []


class _Line:
    def __init__(self, ref_id, kind="service"):
        self.ref_id = ref_id
        self.kind = kind


def test_service_rule_beats_generic_in_resolver():
    """The engine's exceptional-priority resolver must prefer a service-scoped rule
    for that service's line over a generic rule."""
    class R:
        def __init__(self, level, sid=None, pid=None, cat=None):
            self.rule_level = level; self.service_id = sid; self.product_id = pid; self.category = cat
    generic = R("generic")
    svc = R("service", sid="svc-1")
    other_svc = R("service", sid="svc-2")
    picked = CommissionEngine._resolve_exceptional([generic, svc, other_svc], _Line("svc-1"))
    assert picked == [svc]
    # a line with no matching service rule falls back to generic
    picked2 = CommissionEngine._resolve_exceptional([generic, other_svc], _Line("svc-1"))
    assert picked2 == [generic]
