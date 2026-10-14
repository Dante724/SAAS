"""Build 144cs — GDPR consent log + full-org export (extends existing export/erase)."""
import pytest


async def _client(client, h):
    r = await client.post("/api/clients", headers=h, json={"name": "Jane Doe", "email": "jane@x.com", "phone": "555"})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


@pytest.mark.asyncio
async def test_consent_log_records_grant_and_withdraw(client, tenant):
    h = tenant
    cid = await _client(client, h)
    assert (await client.post(f"/api/compliance/gdpr/client/{cid}/consent", headers=h,
            json={"channel": "email", "granted": True, "source": "appointment"})).status_code == 200
    assert (await client.post(f"/api/compliance/gdpr/client/{cid}/consent", headers=h,
            json={"channel": "phone", "granted": False})).status_code == 200
    hist = (await client.get(f"/api/compliance/gdpr/client/{cid}/consent", headers=h)).json()["items"]
    assert len(hist) == 2
    assert hist[0]["channel"] == "phone" and hist[0]["granted"] is False   # newest first
    # bad channel rejected
    assert (await client.post(f"/api/compliance/gdpr/client/{cid}/consent", headers=h,
            json={"channel": "post", "granted": True})).status_code == 400


@pytest.mark.asyncio
async def test_org_export_bundle(client, tenant):
    h = tenant
    await _client(client, h)
    exp = (await client.get("/api/compliance/gdpr/export-org", headers=h)).json()
    assert exp["counts"]["clients"] >= 1
    assert any(c["email"] == "jane@x.com" for c in exp["clients"])
    assert "exported_at" in exp


@pytest.mark.asyncio
async def test_erase_logs_consent_withdrawal(client, tenant):
    h = tenant
    cid = await _client(client, h)
    assert (await client.post(f"/api/compliance/gdpr/client/{cid}/erase", headers=h)).status_code == 200
    hist = (await client.get(f"/api/compliance/gdpr/client/{cid}/consent", headers=h)).json()["items"]
    # erasure logs a withdrawal for all three channels
    assert len([r for r in hist if r["source"] == "erasure" and r["granted"] is False]) == 3
