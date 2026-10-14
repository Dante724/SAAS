"""B1 MFA · B3 rate-limit/offload · B4 observability · B5 white-label · B6 SIEM/bulk."""
import pytest


# ── B1: MFA enroll → activate → login gate → recovery ────────────────────────
@pytest.mark.asyncio
async def test_mfa_enroll_activate_and_login_gate(client, tenant, db):
    from sqlalchemy import select

    from backend.models import Employee
    from backend.services.mfa import totp

    me = (await client.get("/api/auth/me", headers=tenant)).json()

    enroll = (await client.post("/api/mfa/enroll", headers=tenant)).json()
    assert "secret" in enroll and enroll["otpauth_uri"].startswith("otpauth://")
    secret = enroll["secret"]

    # wrong code fails activation
    bad = await client.post("/api/mfa/activate", json={"code": "000000"}, headers=tenant)
    assert bad.status_code == 400

    act = await client.post("/api/mfa/activate", json={"code": totp(secret)}, headers=tenant)
    assert act.status_code == 200
    recovery = act.json()["recovery_codes"]
    assert len(recovery) == 8

    # login now requires MFA
    emp = (await db.execute(select(Employee).where(Employee.id == me["id"]))).scalar_one()
    # no MFA code -> 401 with header
    r = await client.post("/api/auth/token", data={"username": emp.email, "password": "pw-test-12345"})
    assert r.status_code == 401 and r.headers.get("X-MFA-Required") == "1"
    # with valid TOTP in client_secret -> ok
    ok = await client.post("/api/auth/token",
                          data={"username": emp.email, "password": "pw-test-12345", "client_secret": totp(secret)})
    assert ok.status_code == 200 and "access_token" in ok.json()
    # a recovery code also works
    rec = await client.post("/api/auth/token",
                           data={"username": emp.email, "password": "pw-test-12345", "client_secret": recovery[0]})
    assert rec.status_code == 200


@pytest.mark.asyncio
async def test_mfa_disable_requires_password(client, tenant, db):
    from sqlalchemy import select
    from sqlalchemy import update as _update

    from backend.models import Employee, Organisation
    from backend.services.mfa import totp
    # New orgs now mandate MFA (build 144cd), which blocks /disable with a 400 before the password
    # check — clear the mandate so this test can exercise the password gate it's actually about.
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    await db.execute(_update(Organisation).where(Organisation.id == me["org_id"]).values(require_mfa=False))
    await db.commit()
    secret = (await client.post("/api/mfa/enroll", headers=tenant)).json()["secret"]
    await client.post("/api/mfa/activate", json={"code": totp(secret)}, headers=tenant)
    assert (await client.post("/api/mfa/disable", json={"password": "wrong"}, headers=tenant)).status_code == 403
    ok = await client.post("/api/mfa/disable", json={"password": "pw-test-12345"}, headers=tenant)
    assert ok.status_code == 200 and ok.json()["enabled"] is False


# ── B4: metrics + readiness ──────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_metrics_and_readyz(client, tenant):
    await client.get("/api/staff/company", headers=tenant)   # generate some traffic
    m = await client.get("/metrics")
    assert m.status_code == 200 and "repairos_requests_total" in m.text
    assert "repairos_request_latency_seconds_bucket" in m.text
    rz = await client.get("/api/readyz")
    assert rz.status_code == 200 and rz.json()["db"] == "ok"


# ── B5: white-label branding ─────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_branding_roundtrip_and_public_resolver(client, tenant):
    r = await client.put("/api/branding", json={
        "primary_color": "#0A7", "custom_domain": "portal.acme.test",
        "email_from": "Acme <no-reply@acme.test>", "hide_powered_by": True}, headers=tenant)
    assert r.status_code == 200
    b = (await client.get("/api/branding", headers=tenant)).json()
    assert b["primary_color"] == "#0A7" and b["hide_powered_by"] is True

    # public resolver by domain (unauth)
    pub = (await client.get("/api/public/branding?domain=portal.acme.test")).json()
    assert pub["found"] and pub["name"] == b["name"] and pub["hide_powered_by"] is True
    miss = (await client.get("/api/public/branding?domain=nope.test")).json()
    assert miss["found"] is False


# ── B6: SIEM export + bulk API ───────────────────────────────────────────────
@pytest.mark.asyncio
async def test_siem_ndjson_and_bulk(client, tenant):
    import json
    # generate an audited action
    await client.post("/api/clients", json={"name": "SIEM Co"}, headers=tenant)
    nd = await client.get("/api/siem/audit.ndjson", headers=tenant)
    assert nd.status_code == 200 and nd.headers["content-type"].startswith("application/x-ndjson")
    lines = [ln for ln in nd.text.splitlines() if ln.strip()]
    assert lines and all("action" in json.loads(ln) for ln in lines)

    bulk = (await client.get("/api/bulk/clients?limit=100", headers=tenant)).json()
    assert bulk["entity"] == "clients" and any(c["name"] == "SIEM Co" for c in bulk["items"])
    bad = await client.get("/api/bulk/nonsense", headers=tenant)
    assert bad.status_code == 404


# ── B3: field offload helper ─────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_offload_helper_db_vs_local(client, tenant, db, monkeypatch, tmp_path):
    from backend.config import settings
    from backend.services.media import offload_if_enabled
    me = (await client.get("/api/auth/me", headers=tenant)).json()

    monkeypatch.setattr(settings, "MEDIA_BACKEND", "db")
    bid, inline = await offload_if_enabled(db, me["org_id"], "data:image/png;base64,AAAA")
    assert bid is None and inline == "data:image/png;base64,AAAA"   # db keeps inline

    monkeypatch.setattr(settings, "MEDIA_BACKEND", "local")
    monkeypatch.setattr(settings, "MEDIA_LOCAL_DIR", str(tmp_path))
    bid2, inline2 = await offload_if_enabled(db, me["org_id"], "data:image/png;base64,AAAA")
    assert bid2 is not None and inline2 is None                    # local offloads
