"""Launch QA 2026-10-19 (docs/LAUNCH_QA_2026-10-19.md).

B1  the web "New work order" form sends lines with `kind` only — creating an order with items 422'd.
B2  parallel first-load requests seeded duplicate statuses, after which creating an estimate 500'd.
F1  stall detection flagged every new job with a single sample (MIN_N ignored).
B5  the per-IP rate limit counted static assets and locked a whole shop Wi-Fi out.
"""
import asyncio
import importlib.util
import pathlib
import uuid

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import IntegrityError

from backend.models import Employee, OrderStatus

# ── B1 ───────────────────────────────────────────────────────


async def test_web_form_order_with_items_creates(client, tenant):
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    # The exact shape CreateOrderDrawer posted before the fix: kind, no line_type.
    body = {
        "location_id": loc, "complaint": "Cracked screen", "priority": 0,
        "is_estimate": False, "intake_photos": [],
        "lines": [
            {"description": "Screen replacement labour", "kind": "service", "qty": 1, "unit_price": 40},
            {"description": "iPhone 13 screen", "kind": "product", "qty": 1, "unit_price": 120},
            {"description": "Custom line", "kind": "custom", "qty": 1, "unit_price": 5},
        ],
    }
    r = await client.post("/api/orders", json=body, headers=tenant)
    assert r.status_code in (200, 201), r.text
    detail = (await client.get(f"/api/orders/{r.json()['id']}", headers=tenant)).json()
    types = {line["description"]: line["line_type"] for line in detail["lines"]}
    assert types == {"Screen replacement labour": "labour", "iPhone 13 screen": "part", "Custom line": "labour"}


async def test_web_form_payload_with_line_type_still_wins(client, tenant):
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    r = await client.post("/api/orders", json={"location_id": loc, "complaint": "x", "lines": [
        {"description": "Call-out", "kind": "service", "line_type": "fee", "qty": 1, "unit_price": 10}]}, headers=tenant)
    assert r.status_code in (200, 201), r.text
    detail = (await client.get(f"/api/orders/{r.json()['id']}", headers=tenant)).json()
    assert detail["lines"][0]["line_type"] == "fee"


async def test_line_without_type_or_kind_is_rejected(client, tenant):
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    r = await client.post("/api/orders", json={"location_id": loc, "complaint": "x", "lines": [
        {"description": "Mystery", "qty": 1, "unit_price": 10}]}, headers=tenant)
    assert r.status_code == 422


# ── B2 ───────────────────────────────────────────────────────

async def _org_id(db, tenant_headers, client):
    me = (await client.get("/api/auth/me", headers=tenant_headers)).json()
    return (await db.execute(select(Employee.org_id).where(Employee.id == me["id"]))).scalar_one()


async def _dupe_keys(db, org_id):
    return (await db.execute(
        select(OrderStatus.record_type, OrderStatus.key, func.count())
        .where(OrderStatus.org_id == org_id)
        .group_by(OrderStatus.record_type, OrderStatus.key).having(func.count() > 1))).all()


async def test_signup_seeds_every_pipeline(client, tenant, db):
    from backend.routers.statuses import RECORD_TYPES
    org_id = await _org_id(db, tenant, client)
    have = set((await db.execute(select(OrderStatus.record_type).where(OrderStatus.org_id == org_id))).scalars())
    assert have == set(RECORD_TYPES)


async def test_parallel_first_load_seeds_once_and_estimate_creates(client, tenant, db):
    org_id = await _org_id(db, tenant, client)
    # Simulate an org from before signup-seeding: no statuses at all.
    await db.execute(OrderStatus.__table__.delete().where(OrderStatus.org_id == org_id))
    await db.commit()

    paths = ["/api/statuses?record_type=estimate", "/api/statuses?record_type=purchase_order",
             "/api/statuses?record_type=transfer", "/api/statuses", "/api/statuses?record_type=invoice",
             "/api/statuses?record_type=estimate"]
    rs = await asyncio.gather(*(client.get(p, headers=tenant) for p in paths))
    assert all(r.status_code == 200 for r in rs), [r.text for r in rs if r.status_code != 200]
    assert await _dupe_keys(db, org_id) == []

    r = await client.post("/api/orders", json={"complaint": "quote me", "is_estimate": True}, headers=tenant)
    assert r.status_code in (200, 201), r.text
    assert r.json().get("status", "est_draft") == "est_draft"


async def test_concurrent_ensure_seeded_sessions(client, tenant, db):
    """Several sessions seeding the same empty org at once never produce duplicates."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from backend.routers.statuses import ensure_seeded
    # Sessions on the fixture's engine. (Importing conftest's TestSession would re-run conftest as a
    # second module under xdist and re-point get_db at an empty per-worker DB.)
    TestSession = async_sessionmaker(db.bind, expire_on_commit=False)
    org_id = await _org_id(db, tenant, client)
    await db.execute(OrderStatus.__table__.delete().where(OrderStatus.org_id == org_id))
    await db.commit()

    async def seed():
        async with TestSession() as s:
            await ensure_seeded(s, org_id)
    await asyncio.gather(*(seed() for _ in range(6)))
    assert await _dupe_keys(db, org_id) == []
    n = (await db.execute(select(func.count()).select_from(OrderStatus).where(
        OrderStatus.org_id == org_id, OrderStatus.record_type == "estimate"))).scalar()
    assert n == 7


async def test_unique_index_blocks_duplicate_key(client, tenant, db):
    org_id = await _org_id(db, tenant, client)
    db.add(OrderStatus(org_id=org_id, key="est_draft", name="Draft", record_type="estimate", is_estimate=True))
    with pytest.raises(IntegrityError):
        await db.commit()
    await db.rollback()
    # Same key in a different pipeline is still fine (bare-key lookups filter by is_estimate).
    db.add(OrderStatus(org_id=org_id, key="est_draft", name="Draft", record_type="work_order"))
    await db.commit()


async def test_create_status_skips_inactive_key(client, tenant):
    r1 = await client.post("/api/statuses", json={"name": "On hold", "status_group": "pending"}, headers=tenant)
    assert r1.status_code == 201, r1.text
    sid = r1.json()["id"]
    await client.delete(f"/api/statuses/{sid}", headers=tenant)
    r2 = await client.post("/api/statuses", json={"name": "On hold", "status_group": "pending"}, headers=tenant)
    assert r2.status_code == 201, r2.text


async def test_estimate_create_with_two_new_group_statuses(client, tenant):
    r = await client.post("/api/statuses", json={"name": "Quote request", "status_group": "new",
                                                  "record_type": "estimate"}, headers=tenant)
    assert r.status_code == 201, r.text
    r = await client.post("/api/orders", json={"complaint": "quote", "is_estimate": True}, headers=tenant)
    assert r.status_code in (200, 201), r.text


def _migration():
    p = pathlib.Path(__file__).resolve().parents[1] / "migrations" / "versions" / "os144q5_order_status_unique_key.py"
    spec = importlib.util.spec_from_file_location("os144q5", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_migration_dedupes_and_repoints(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'm.db'}")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE order_statuses (id TEXT PRIMARY KEY, org_id TEXT, record_type TEXT, "
                       "key TEXT, active BOOLEAN, sort_order INTEGER)"))
        c.execute(text("CREATE TABLE status_transitions (id TEXT PRIMARY KEY, org_id TEXT, "
                       "from_status_id TEXT, to_status_id TEXT, role_id TEXT)"))
        c.execute(text("CREATE TABLE feedback_settings (id TEXT PRIMARY KEY, trigger_status_id TEXT)"))
        c.execute(text("INSERT INTO order_statuses VALUES "
                       "('a1','o','estimate','est_sent',1,2),('a2','o','estimate','est_sent',1,2),"
                       "('a3','o','estimate','est_sent',0,0),('b1','o','estimate','est_draft',1,0),"
                       "('b2','o','estimate','est_draft',1,0),('w1','o','work_order','est_sent',1,0)"))
        c.execute(text("INSERT INTO status_transitions VALUES ('t1','o','b1','a1',NULL),"
                       "('t2','o','b2','a2',NULL),('t3','o','b1','a3',NULL)"))
        c.execute(text("INSERT INTO feedback_settings VALUES ('f1','a3')"))
        _migration()._dedupe(c)
        ids = set(c.execute(text("SELECT id FROM order_statuses")).scalars())
        assert ids == {"a1", "b1", "w1"}   # active first, then sort_order, then id; other pipelines untouched
        trans = c.execute(text("SELECT from_status_id, to_status_id FROM status_transitions")).all()
        assert trans == [("b1", "a1")]
        assert c.execute(text("SELECT trigger_status_id FROM feedback_settings")).scalar() == "a1"


# ── F1 ───────────────────────────────────────────────────────

def test_stall_needs_min_samples():
    from backend.services.cycle_time import MIN_N, is_stalled
    assert not is_stalled(elapsed_min=500, typical_min=10, n=1)
    assert not is_stalled(elapsed_min=500, typical_min=10, n=MIN_N - 1)
    assert is_stalled(elapsed_min=500, typical_min=10, n=MIN_N)


def test_stall_has_a_floor():
    from backend.services.cycle_time import MIN_N, STALL_FLOOR_MIN, is_stalled
    # "Running long 0 min vs ~0 min typical" — tiny typical times never flag.
    assert not is_stalled(elapsed_min=0, typical_min=0, n=MIN_N * 4)
    assert not is_stalled(elapsed_min=STALL_FLOOR_MIN - 1, typical_min=1, n=MIN_N * 4)
    assert is_stalled(elapsed_min=STALL_FLOOR_MIN * 3, typical_min=1, n=MIN_N * 4)


# ── B5 ───────────────────────────────────────────────────────

@pytest.fixture
def tight_limit(monkeypatch):
    from backend.config import settings
    from backend.services import ratelimit
    monkeypatch.setattr(ratelimit, "_redis", False)    # in-process store
    monkeypatch.setattr(ratelimit, "_aredis", False)
    monkeypatch.setattr(settings, "RATE_LIMIT_IP_PER_MIN", 5)
    monkeypatch.setattr(settings, "RATE_LIMIT_USER_PER_MIN", 8)
    ratelimit._HITS.clear()
    yield ratelimit._HITS.clear
    ratelimit._HITS.clear()


async def test_static_assets_not_counted(client, tight_limit):
    for path in ["/assets/index-abc.js", "/favicon.ico", "/logo.svg"] * 7:
        r = await client.get(path)
        assert r.status_code != 429, path
    assert (await client.get("/api/auth/me")).status_code != 429


async def test_anonymous_api_limited_per_ip(client, tight_limit):
    codes = [(await client.get("/api/auth/me")).status_code for _ in range(7)]
    assert codes[:5] == [401] * 5 and 429 in codes


async def test_forged_token_counts_against_ip(client, tight_limit):
    import jwt
    bad = {"Authorization": "Bearer " + jwt.encode({"sub": "x"}, "not-the-key-" * 4, algorithm="HS256")}
    codes = [(await client.get("/api/auth/me", headers=bad)).status_code for _ in range(7)]
    assert 429 in codes


async def test_signed_in_users_on_one_ip_have_own_budgets(client, tenant, tight_limit):
    # A second user, same IP (registration itself is anonymous traffic, so reset after).
    email = f"b-{uuid.uuid4().hex[:6]}@test.dev"
    r = await client.post("/api/auth/register", json={"org_name": "Other", "name": "B", "email": email,
                                                      "password": "pw-test-12345"})
    other = {"Authorization": f"Bearer {r.json()['access_token']}"}
    tight_limit()
    # 6 each = 12 from one IP: over the anonymous per-IP ceiling (5), under each user's budget (8).
    for h in (tenant, other) * 6:
        r = await client.get("/api/auth/me", headers=h)
        assert r.status_code == 200, r.status_code
    # Anonymous traffic from the same IP is unaffected by the signed-in traffic.
    assert (await client.get("/api/auth/me")).status_code == 401
    codes = [(await client.get("/api/auth/me", headers=tenant)).status_code for _ in range(4)]
    assert 429 in codes   # each user still has a ceiling


# ── F10 ──────────────────────────────────────────────────────

def test_demo_passwords_generated_once_and_env_wins(tmp_path, monkeypatch):
    from backend.services.auth import is_old_default_password
    from scripts import demo_credentials as dc
    monkeypatch.setattr(dc, "DEMO_FILE", tmp_path / ".env.demo")
    monkeypatch.delenv("DEMO_ADMIN_PASSWORD", raising=False)
    monkeypatch.delenv("DEMO_STAFF_PASSWORD", raising=False)
    first = dc.demo_passwords()
    assert dc.demo_passwords() == first           # stable across seeds/resets
    assert not any(is_old_default_password(p) or len(p) < 12 for p in first)
    monkeypatch.setenv("DEMO_ADMIN_PASSWORD", "env-admin-pass-1")
    assert dc.demo_passwords()[0] == "env-admin-pass-1"


async def test_seeded_demo_accounts_sign_in(client, tmp_path, monkeypatch):
    from scripts import demo_credentials as dc
    from scripts.seed import seed_demo_data
    monkeypatch.setattr(dc, "DEMO_FILE", tmp_path / ".env.demo")
    monkeypatch.setenv("DEMO_ADMIN_PASSWORD", "demo-admin-test-pw")
    monkeypatch.setenv("DEMO_STAFF_PASSWORD", "demo-staff-test-pw")
    await seed_demo_data()
    for email, pw in [("admin@repairos.dev", "demo-admin-test-pw"), ("priya@repairos.dev", "demo-staff-test-pw")]:
        r = await client.post("/api/auth/token", data={"username": email, "password": pw})
        assert r.status_code == 200, r.text
    r = await client.post("/api/auth/token", data={"username": "admin@repairos.dev", "password": "admin123"})
    assert r.status_code == 401
