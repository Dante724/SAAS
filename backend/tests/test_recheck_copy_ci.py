"""Launch recheck (B1, B12, B13): honest plan copy, AI drafts without made-up prices, viewed pay
links stay payable, the Postgres CI job is a required gate, and the committed legal pages can't
ship [[...]] placeholders without CI failing."""
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from backend.models import Organisation, PaymentIntent
from backend.services import ai

REPO = Path(__file__).resolve().parents[2]
CHECK = REPO / "website" / "tools" / "check_legal_placeholders.py"


async def _loc(client, tenant):
    return (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]


# ── B13: no Xero / two-way sync claim where shops pay ────────────────────────

@pytest.mark.asyncio
async def test_billing_page_and_paywall_do_not_sell_xero_sync(client, tenant, db):
    plans = (await client.get("/api/billing", headers=tenant)).json()["plans"]
    features = [f for p in plans for f in p["features"]]
    assert features and not any("Xero" in f or "sync" in f.lower() for f in features)
    assert any("QuickBooks Online" in f for f in features)

    me = (await client.get("/api/auth/me", headers=tenant)).json()
    org = await db.get(Organisation, me["org_id"])
    org.sub_status, org.plan = "active", "shop"
    await db.commit()
    r = await client.get("/api/integrations/qbo/status", headers=tenant)
    assert r.status_code == 403
    assert "Xero" not in r.json()["detail"] and "sync" not in r.json()["detail"].lower()


# ── B13: "Draft from the complaint" with no AI set up ────────────────────────

@pytest.mark.asyncio
async def test_ai_draft_without_ai_puts_no_made_up_prices_on_the_estimate(client, tenant, monkeypatch):
    monkeypatch.setattr(ai, "is_live", lambda: False)
    r = await client.post("/api/ai/estimate/draft", json={"complaint": "cracked screen"}, headers=tenant)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["provider"] == "stub" and d["lines"]
    assert all(line["unit_price"] == 0 for line in d["lines"])
    assert "no prices" in d["note"]


# ── NEW: a pay link the customer has opened is still payable ─────────────────

@pytest.mark.asyncio
async def test_viewed_pay_link_still_shows_a_pay_button(client, tenant, db):
    o = (await client.post("/api/orders", json={"location_id": await _loc(client, tenant), "complaint": "x"},
                           headers=tenant)).json()
    await client.post(f"/api/orders/{o['id']}/lines", json={
        "line_type": "part", "kind": "custom", "description": "Screen", "qty": 1, "unit_price": 50.0},
        headers=tenant)
    intent = (await client.post(f"/api/orders/{o['id']}/payment-link", json={}, headers=tenant)).json()["intent_id"]
    # The test provider opens a session straight away (pending); a link not yet handed to a
    # provider sits at "created", which is what the customer opening it moves to "viewed".
    pi = await db.get(PaymentIntent, intent)
    pi.status = "created"
    await db.commit()
    v = await client.post(f"/api/payments/intents/{intent}/view")
    assert v.status_code == 200 and v.json()["status"] == "viewed"
    info = (await client.get(f"/api/public/pay/{intent}")).json()
    assert info["status"] == "viewed" and info["payable"] is True


# ── B1: the Postgres job is a required gate ──────────────────────────────────

def test_backend_postgres_ci_job_is_required():
    ci = yaml.safe_load((REPO / ".github" / "workflows" / "ci.yml").read_text())
    job = ci["jobs"]["backend-postgres"]
    assert "continue-on-error" not in job
    assert "ADVISORY" not in (REPO / ".github" / "workflows" / "ci.yml").read_text()
    for name, j in ci["jobs"].items():
        assert not j.get("continue-on-error"), name


# ── B12: committed legal pages can't ship placeholders silently ──────────────

def _check(*args):
    return subprocess.run([sys.executable, str(CHECK), *args], capture_output=True, text=True)


def test_legal_placeholder_check_fails_on_a_served_page(tmp_path):
    (tmp_path / "terms").mkdir()
    (tmp_path / "terms" / "index.html").write_text("<p>Run by [[COMPANY LEGAL NAME]].</p>")
    (tmp_path / "tools" / "legal").mkdir(parents=True)
    (tmp_path / "tools" / "legal" / "terms.html").write_text("[[JURISDICTION]]")   # a source: not served
    (tmp_path / "index.html").write_text('<div data-fields=\'[["Device","Phone"]]\'></div>')  # not a token
    r = _check(str(tmp_path))
    assert r.returncode == 1
    assert "[[COMPANY LEGAL NAME]]" in r.stdout and "[[JURISDICTION]]" not in r.stdout
    assert "::error file=website/terms/index.html,line=1::" in r.stdout
    warn = _check("--warn-only", str(tmp_path))
    assert warn.returncode == 0 and "::warning" in warn.stdout

    (tmp_path / "terms" / "index.html").write_text("<p>Run by Example Ltd.</p>")
    assert _check(str(tmp_path)).returncode == 0


def test_legal_placeholder_check_matches_the_committed_pages():
    pages = [REPO / "website" / "terms" / "index.html", REPO / "website" / "privacy" / "index.html"]
    left = any("[[" in p.read_text() and _check_token(p.read_text()) for p in pages)
    r = _check()
    assert r.returncode == (1 if left else 0), r.stdout
    # CI runs it, failing for main and pull requests into main.
    ci = yaml.safe_load((REPO / ".github" / "workflows" / "ci.yml").read_text())
    run = " ".join(s.get("run", "") for s in ci["jobs"]["website-legal"]["steps"])
    assert "check_legal_placeholders.py" in run and 'GITHUB_BASE_REF" = "main"' in run


def _check_token(text):
    import re
    return re.search(r"\[\[[A-Z][^\]]*\]\]", text)
