"""Owner decision 7: a lapsed shop gets a week's grace, then is treated exactly like a suspended one.

A shop is lapsed when its term end (``sub_end``) has passed and it isn't suspended. For
SUB_LAPSE_GRACE_DAYS (7) days after sub_end it keeps normal service and its owner sees a "renew"
banner (/api/auth/me says lapsed + grace_ends_at). After that, every suspension gate applies
(sign-in, staff tokens, API keys, background jobs, public pages). The rule lives in one place:
services/org_live. The suspension gate tests (test_w4_suspension_gaps, test_p0_status_push_suspension)
also run every case against a shop past its grace.
"""
import re
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from backend.config import settings
from backend.models import Organisation
from backend.services import org_live
from backend.services.dates import platform_today

pytestmark = pytest.mark.asyncio

TODAY = date(2026, 11, 14)


def _org(sub_end=None, sub_status="active", active=True):
    return SimpleNamespace(sub_end=sub_end, sub_status=sub_status, active=active, suspend_message=None)


# ── The rule, on a loaded shop ───────────────────────────────────────────────────────────────────
async def test_grace_is_one_setting_defaulting_to_a_week():
    assert settings.SUB_LAPSE_GRACE_DAYS == 7
    assert org_live.grace_days() == 7


@pytest.mark.parametrize("days_past", [1, 2, 7])
async def test_inside_grace_the_shop_is_lapsed_but_live(days_past):
    org = _org(sub_end=TODAY - timedelta(days=days_past))
    assert org_live.org_lapsed(org, TODAY) is True
    assert org_live.org_is_live(org, TODAY) is True
    assert org_live.off_reason(org, TODAY) is None
    assert org_live.grace_ends_at(org) == org.sub_end + timedelta(days=7)
    assert org_live.lapse_info(org, TODAY) == {
        "lapsed": True, "sub_end": org.sub_end.isoformat(),
        "grace_ends_at": (org.sub_end + timedelta(days=7)).isoformat()}


@pytest.mark.parametrize("days_past", [8, 9, 400])
async def test_after_grace_the_shop_is_off_like_a_suspended_one(days_past):
    org = _org(sub_end=TODAY - timedelta(days=days_past))
    assert org_live.org_is_live(org, TODAY) is False
    assert org_live.off_reason(org, TODAY) == "lapsed"
    assert org_live.lapse_info(org, TODAY)["lapsed"] is True


@pytest.mark.parametrize("sub_end", [None, TODAY, TODAY + timedelta(days=1), TODAY + timedelta(days=300)])
async def test_a_term_still_running_or_unset_is_unaffected(sub_end):
    org = _org(sub_end=sub_end)
    assert org_live.org_lapsed(org, TODAY) is False      # sub_end is the term's last day
    assert org_live.org_is_live(org, TODAY) is True
    assert org_live.off_reason(org, TODAY) is None
    assert org_live.lapse_info(org, TODAY) == {"lapsed": False, "sub_end": sub_end and sub_end.isoformat(),
                                               "grace_ends_at": None}


async def test_a_suspended_shop_is_suspended_not_lapsed_and_gets_no_grace():
    org = _org(sub_end=TODAY - timedelta(days=1), sub_status="suspended")
    assert org_live.org_lapsed(org, TODAY) is False
    assert org_live.off_reason(org, TODAY) == "suspended"
    assert org_live.org_is_live(org, TODAY) is False


async def test_grace_length_follows_the_setting(monkeypatch):
    monkeypatch.setattr(settings, "SUB_LAPSE_GRACE_DAYS", 3)
    assert org_live.org_is_live(_org(sub_end=TODAY - timedelta(days=3)), TODAY) is True
    assert org_live.org_is_live(_org(sub_end=TODAY - timedelta(days=4)), TODAY) is False


async def test_staff_lockout_waits_out_the_grace():
    from backend.services.auth import assert_org_active
    assert_org_active(_org(sub_end=TODAY - timedelta(days=7)), TODAY)          # last grace day: open
    with pytest.raises(HTTPException) as e:
        assert_org_active(_org(sub_end=TODAY - timedelta(days=8)), TODAY)
    assert e.value.status_code == 403 and "subscription has ended" in e.value.detail


# ── The same rule in SQL ─────────────────────────────────────────────────────────────────────────
async def test_sql_clause_agrees_with_the_python_rule(db):
    today = platform_today()
    cases = {}
    for label, sub_end, status in [
        ("none", None, "active"), ("future", today + timedelta(days=5), "active"),
        ("today", today, "active"), ("d1", today - timedelta(days=1), "active"),
        ("d7", today - timedelta(days=7), "active"), ("d8", today - timedelta(days=8), "active"),
        ("old", today - timedelta(days=90), "trial"), ("susp", None, "suspended"),
        ("susp_grace", today - timedelta(days=2), "suspended"), ("off", None, "active"),
    ]:
        o = Organisation(name=f"Lapse {label}", sub_end=sub_end, sub_status=status, active=label != "off")
        db.add(o)
        cases[label] = o
    await db.commit()
    ids = [o.id for o in cases.values()]
    live = set((await db.execute(select(Organisation.id).where(
        Organisation.id.in_(ids), org_live.live_org_clause()))).scalars().all())
    for label, o in cases.items():
        assert (o.id in live) is org_live.org_is_live(o), label
        assert await org_live.org_id_is_live(db, o.id) is org_live.org_is_live(o), label
    assert {lbl for lbl, o in cases.items() if o.id in live} == {
        "none", "future", "today", "d1", "d7"}


# ── End to end ───────────────────────────────────────────────────────────────────────────────────
async def _register(client):
    import uuid
    email = f"lapse-{uuid.uuid4().hex[:8]}@test.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Lapse Co", "name": "Owner", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    org_id = (await client.get("/api/auth/me", headers=h)).json()["org_id"]
    return email, h, org_id


async def _set_sub_end(db, org_id, sub_end):
    org = (await db.execute(select(Organisation).where(Organisation.id == org_id))).scalar_one()
    org.sub_end = sub_end
    await db.commit()


async def _login(client, email):
    return await client.post("/api/auth/token", data={"username": email, "password": "pw-test-12345"})


@pytest.mark.parametrize("days_past", [1, 7])
async def test_in_grace_everything_works_and_me_says_lapsed(client, db, days_past):
    email, h, org_id = await _register(client)
    sub_end = platform_today() - timedelta(days=days_past)
    await _set_sub_end(db, org_id, sub_end)

    me = await client.get("/api/auth/me", headers=h)
    assert me.status_code == 200
    body = me.json()
    assert body["subscription"] == {"lapsed": True, "sub_end": sub_end.isoformat(),
                                    "grace_ends_at": (sub_end + timedelta(days=7)).isoformat()}
    assert (await _login(client, email)).status_code == 200
    assert (await client.post("/api/clients", json={"name": "Still open"}, headers=h)).status_code in (200, 201)
    assert (await client.get(f"/api/public/appointment/{org_id}")).status_code == 200
    assert await org_live.org_id_is_live(db, org_id) is True


async def test_after_grace_sign_in_and_tokens_are_refused(client, db):
    email, h, org_id = await _register(client)
    await _set_sub_end(db, org_id, platform_today() - timedelta(days=8))
    r = await client.get("/api/auth/me", headers=h)
    assert r.status_code == 403 and "subscription has ended" in r.text
    assert (await _login(client, email)).status_code == 403
    assert (await client.get(f"/api/public/appointment/{org_id}")).status_code == 403
    assert await org_live.org_id_is_live(db, org_id) is False

    await _set_sub_end(db, org_id, platform_today() + timedelta(days=30))    # renewed
    assert (await client.get("/api/auth/me", headers=h)).status_code == 200
    assert (await client.get(f"/api/public/appointment/{org_id}")).status_code == 200


@pytest.mark.parametrize("sub_end_days", [None, 0, 10])
async def test_me_says_not_lapsed_while_the_term_runs(client, db, sub_end_days):
    _email, h, org_id = await _register(client)
    sub_end = None if sub_end_days is None else platform_today() + timedelta(days=sub_end_days)
    await _set_sub_end(db, org_id, sub_end)
    body = (await client.get("/api/auth/me", headers=h)).json()
    assert body["subscription"]["lapsed"] is False and body["subscription"]["grace_ends_at"] is None


# ── One place for the rule ───────────────────────────────────────────────────────────────────────
# Comparing sub_status to "suspended", or sub_end to a day, decides whether a shop is open; that is
# org_live's job. The admin portal (which sets and reports the flag itself) is the one exception.
_ALLOWED = {"services/org_live.py", "routers/admin.py", "services/admin_access.py"}
_STRAY = [
    re.compile(r"""sub_status\s*(==|!=)\s*["']suspended["']"""),
    re.compile(r"""["']suspended["']\s*(==|!=)\s*\w*\.?sub_status"""),
    re.compile(r"""sub_end\s*(<|>|<=|>=)"""),
    re.compile(r"""(<|>|<=|>=)\s*\w+\.sub_end\b"""),
]


async def test_the_lapse_and_suspension_rule_is_written_only_in_org_live():
    root = Path(__file__).resolve().parents[1]
    stray = []
    for path in root.rglob("*.py"):
        rel = path.relative_to(root).as_posix()
        if rel.startswith(("tests/", "migrations/")) or rel in _ALLOWED:
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if any(p.search(line) for p in _STRAY):
                stray.append(f"{rel}:{n}: {line.strip()}")
    assert stray == [], "use services/org_live instead:\n" + "\n".join(stray)
