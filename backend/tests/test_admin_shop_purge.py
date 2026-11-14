"""Owner decision 9: a platform owner can hard-purge an offboarded shop from /admin.

Only an owner, only N days (setting, default 30, max 90) after offboarding, only after typing the
shop's exact name; a dry run shows the counts first; the purge is logged; every row of the shop and
its media files go, and no other shop loses a row or a file. Nothing runs it on a schedule.
"""
import base64
import inspect
import os
import pathlib
import re
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, insert, select

from backend.config import settings
from backend.db.database import AsyncSessionLocal, Base
from backend.models import (
    AdminActionLog,
    AdminRole,
    AuthToken,
    ClientPortalLink,
    Employee,
    MediaBlob,
    Organisation,
    Payment,
    RevokedSession,
)
from backend.services import admin_log, media
from backend.services.offboarding import offboard_org
from backend.tests.admin_helpers import admin_headers

PNG = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
DATA_URL = f"data:image/png;base64,{PNG}"


@pytest.fixture(autouse=True)
def _local_media(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "MEDIA_BACKEND", "local")
    monkeypatch.setattr(settings, "MEDIA_LOCAL_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "SHOP_PURGE_AFTER_DAYS", 30)
    return tmp_path


async def _shop(client, name):
    r = await client.post("/api/auth/register", json={
        "org_name": name, "name": "Owner", "email": f"pg-{uuid.uuid4().hex[:8]}@test.dev",
        "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    me = (await client.get("/api/auth/me", headers=h)).json()
    loc = (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]
    cl = (await client.post("/api/clients", json={"name": f"{name} client", "phone": "+15550100"},
                            headers=h)).json()["id"]
    o = (await client.post("/api/orders", json={"client_id": cl, "location_id": loc, "complaint": "cracked"},
                           headers=h)).json()
    up = await client.post("/api/media", json={"data": DATA_URL, "name": "same.png", "mime": "image/png"}, headers=h)
    assert up.status_code == 201, up.text
    s = {"h": h, "org": me["org_id"], "order": o["id"], "client": cl, "blob": up.json()["id"], "name": name}
    async with AsyncSessionLocal() as db:
        emp = (await db.execute(select(Employee.id).where(Employee.org_id == s["org"]))).scalar()
        db.add(Payment(org_id=s["org"], order_id=s["order"], method="cash", amount=25))
        # Rows with no org_id of their own: they belong to the shop through its staff.
        db.add(AuthToken(kind="reset", employee_id=emp, token_hash=uuid.uuid4().hex, expires_at=datetime.utcnow()))
        db.add(RevokedSession(sid=uuid.uuid4().hex, employee_id=emp, expires_at=datetime.utcnow()))
        await db.commit()
    s["emp"] = emp
    return s


async def _offboard(org_id, days_ago):
    async with AsyncSessionLocal() as db:
        await offboard_org(db, org_id, actor="ops@tekly.app", reason="Closed",
                           now=datetime.utcnow() - timedelta(days=days_ago))
        await db.commit()


async def _counts(org_id) -> dict:
    """Rows per table that belong to this shop (org-less rows through its staff), plus its
    organisations row and action-log rows."""
    out = {}
    async with AsyncSessionLocal() as db:
        emps = select(Employee.id).where(Employee.org_id == org_id).scalar_subquery()
        for t in Base.metadata.sorted_tables:
            if t.name == "organisations":
                q = select(func.count()).select_from(t).where(t.c.id == org_id)
            elif "org_id" in t.c:
                q = select(func.count()).select_from(t).where(t.c.org_id == org_id)
            elif "employee_id" in t.c:
                q = select(func.count()).select_from(t).where(t.c.employee_id.in_(emps))
            else:
                continue
            out[t.name] = (await db.execute(q)).scalar()
    return out


async def _blob_bytes(blob_id):
    async with AsyncSessionLocal() as db:
        blob = (await db.execute(select(MediaBlob).where(MediaBlob.id == blob_id))).scalar_one()
        return (await media.load(blob))[0]


def _files(root) -> set:
    return {str(p.relative_to(root)) for p in pathlib.Path(root).rglob("*") if p.is_file()}


async def _purge(client, H, s, *, name=None, reason="Closed 2026, retention passed"):
    return await client.post(f"/api/admin/tenants/{s['org']}/purge",
                             json={"confirm_name": s["name"] if name is None else name, "reason": reason}, headers=H)


# ── who can reach it ───────────────────────────────────────────────────────────────────────────
async def test_signed_out_and_shop_users_cannot_reach_the_purge(client):
    a = await _shop(client, "Gone Co")
    await _offboard(a["org"], 40)
    for headers in ({}, a["h"]):
        assert (await client.get(f"/api/admin/tenants/{a['org']}/purge", headers=headers)).status_code == 401
        assert (await _purge(client, headers, a)).status_code == 401
    assert (await _counts(a["org"]))["orders"] == 1


@pytest.mark.parametrize("role", ["support", "sales_am", "account_manager", "sales"])
async def test_an_admin_role_without_the_permission_gets_403_and_it_is_logged(client, role):
    a = await _shop(client, "Gone Co")
    await _offboard(a["org"], 40)
    H, _ = await admin_headers(client, role=role)
    assert (await client.get(f"/api/admin/tenants/{a['org']}/purge", headers=H)).status_code == 403
    assert (await _purge(client, H, a)).status_code == 403
    assert (await _counts(a["org"]))["orders"] == 1
    async with AsyncSessionLocal() as db:
        refused = (await db.execute(select(AdminActionLog).where(
            AdminActionLog.action == "purge_shops", AdminActionLog.outcome == "refused"))).scalars().all()
        assert len(refused) == 2


async def test_only_the_owner_role_can_hold_the_purge_permission(client):
    H, _ = await admin_headers(client)
    me = (await client.get("/api/admin/me", headers=H)).json()
    assert me["permissions"]["purge_shops"] is True
    r = await client.post("/api/admin/roles", json={"name": "Data team", "permissions": {"purge_shops": True,
                                                                                        "view_shops": True},
                                                     "reason": "Try to delegate it"}, headers=H)
    assert r.status_code == 201, r.text
    async with AsyncSessionLocal() as db:
        role = await db.get(AdminRole, r.json()["id"])
        assert "purge_shops" not in (role.permissions or {})
    roles = (await client.get("/api/admin/roles", headers=H)).json()
    codes = {p["code"] for g in roles["catalog"]["permissions"].values() for p in g}
    assert "purge_shops" not in codes          # never offered in the role editor


async def test_an_owner_role_row_from_before_the_permission_still_gets_it(client):
    """The owner role's stored permissions were written when it was created; the owner role holds
    every permission whatever the row says (it can't be edited)."""
    H, _ = await admin_headers(client)
    async with AsyncSessionLocal() as db:
        role = (await db.execute(select(AdminRole).where(AdminRole.builtin == "owner"))).scalar_one()
        role.permissions = {k: v for k, v in role.permissions.items() if k != "purge_shops"}
        await db.commit()
    assert (await client.get("/api/admin/me", headers=H)).json()["permissions"]["purge_shops"] is True


# ── when it is allowed ─────────────────────────────────────────────────────────────────────────
async def test_a_shop_that_is_not_offboarded_is_refused(client):
    a = await _shop(client, "Open Co")
    H, _ = await admin_headers(client)
    dry = (await client.get(f"/api/admin/tenants/{a['org']}/purge", headers=H)).json()
    assert dry["eligible"] is False and dry["offboarded_at"] is None and "offboarded" in dry["why_not"]
    r = await _purge(client, H, a)
    assert r.status_code == 409 and "offboarded" in r.json()["detail"]
    assert (await _counts(a["org"]))["orders"] == 1


async def test_a_shop_offboarded_less_than_n_days_ago_is_refused(client):
    a = await _shop(client, "Recent Co")
    await _offboard(a["org"], 29)
    H, _ = await admin_headers(client)
    dry = (await client.get(f"/api/admin/tenants/{a['org']}/purge", headers=H)).json()
    assert dry["eligible"] is False and dry["after_days"] == 30 and dry["eligible_from"]
    r = await _purge(client, H, a)
    assert r.status_code == 409 and "30 days" in r.json()["detail"]
    assert (await _counts(a["org"]))["orders"] == 1


def test_the_waiting_period_defaults_to_30_and_never_exceeds_90(monkeypatch):
    from backend.config import Settings
    from backend.services import shop_purge
    assert Settings.model_fields["SHOP_PURGE_AFTER_DAYS"].default == 30
    for value, want in ((30, 30), (90, 90), (91, 90), (365, 90), (7, 7), (0, 1), (-5, 1)):
        monkeypatch.setattr(settings, "SHOP_PURGE_AFTER_DAYS", value)
        assert shop_purge.purge_after_days() == want, value


async def test_a_setting_above_90_is_clamped_to_90(client, monkeypatch):
    monkeypatch.setattr(settings, "SHOP_PURGE_AFTER_DAYS", 400)
    a = await _shop(client, "Long Wait Co")
    await _offboard(a["org"], 89)
    H, _ = await admin_headers(client)
    dry = (await client.get(f"/api/admin/tenants/{a['org']}/purge", headers=H)).json()
    assert dry["after_days"] == 90 and dry["eligible"] is False


async def test_a_wrong_typed_name_is_refused(client):
    a = await _shop(client, "Exact Name Co")
    await _offboard(a["org"], 31)
    H, _ = await admin_headers(client)
    for typed in ("", "exact name co", "Exact Name", "Exact Name Co ", a["org"]):
        r = await _purge(client, H, a, name=typed)
        assert r.status_code in (409, 422) and r.status_code != 200, typed
    assert (await _counts(a["org"]))["orders"] == 1
    async with AsyncSessionLocal() as db:
        assert not (await db.execute(select(AdminActionLog).where(
            AdminActionLog.action == "purge_shop", AdminActionLog.outcome == "allowed"))).first()


# ── dry run ────────────────────────────────────────────────────────────────────────────────────
async def test_the_dry_run_counts_every_table_and_deletes_nothing(client, _local_media):
    a = await _shop(client, "Dry Co")
    await _offboard(a["org"], 31)
    before, files = await _counts(a["org"]), _files(_local_media)
    H, _ = await admin_headers(client)
    r = await client.get(f"/api/admin/tenants/{a['org']}/purge", headers=H)
    assert r.status_code == 200, r.text
    dry = r.json()
    assert dry["eligible"] is True and dry["confirm_name"] == "Dry Co"
    c = dry["counts"]
    assert c["orders"] == 1 and c["clients"] == 1 and c["payments"] == 1 and c["media_blobs"] == 1
    assert c["employees"] == 1 and c["revoked_sessions"] == 1
    assert c["auth_tokens"] == before["auth_tokens"] >= 1      # reached through the shop's staff
    assert {t: n for t, n in before.items() if n and t not in ("organisations", "admin_action_log")} == c
    assert "admin_action_log" not in c and "organisations" not in c
    assert dry["media_files"] == 1 and dry["total"] == sum(c.values())
    assert all(v > 0 for v in c.values())          # only tables with rows are listed
    assert await _counts(a["org"]) == before and _files(_local_media) == files


# ── the purge itself, and the shop next door ───────────────────────────────────────────────────
async def test_purge_removes_every_row_and_file_of_the_shop_and_nothing_of_another(client, _local_media):
    a = await _shop(client, "Leaving Co")
    b = await _shop(client, "Staying Co")          # same kinds of rows, the identical file
    H, _ = await admin_headers(client)
    # Admin-side rows about both shops (notes live in admin_shop_notes, with org_id).
    for s in (a, b):
        assert (await client.post(f"/api/admin/tenants/{s['org']}/notes", json={"body": "Called"},
                                  headers=H)).status_code == 201
    await _offboard(a["org"], 45)
    await _offboard(b["org"], 45)                   # B is just as purgeable; only A is purged
    a_before, b_before = await _counts(a["org"]), await _counts(b["org"])
    assert a_before["orders"] == b_before["orders"] == 1
    b_file = await _blob_bytes(b["blob"])
    assert b_file == base64.b64decode(PNG) == await _blob_bytes(a["blob"])
    async with AsyncSessionLocal() as db:
        a_key = (await db.get(MediaBlob, a["blob"])).storage_key
        b_key = (await db.get(MediaBlob, b["blob"])).storage_key
    assert os.path.isfile(os.path.join(_local_media, a_key)) and os.path.isfile(os.path.join(_local_media, b_key))

    dry = (await client.get(f"/api/admin/tenants/{a['org']}/purge", headers=H)).json()
    r = await _purge(client, H, a)
    assert r.status_code == 200, r.text
    assert r.json()["counts"] == dry["counts"] and r.json()["media_files"] == 1

    a_after = await _counts(a["org"])
    leftovers = {t: n for t, n in a_after.items() if n and t not in ("organisations", "admin_action_log")}
    assert leftovers == {}                          # every org-scoped table: no row of A left
    assert not os.path.exists(os.path.join(_local_media, a_key))
    # The shop next door: every table, row for row, and its file still loads.
    assert await _counts(b["org"]) == b_before
    assert os.path.isfile(os.path.join(_local_media, b_key))
    assert await _blob_bytes(b["blob"]) == b_file
    assert (await client.get(f"/api/admin/tenants/{b['org']}", headers=H)).json()["name"] == "Staying Co"

    async with AsyncSessionLocal() as db:
        org = await db.get(Organisation, a["org"])   # a scrubbed tombstone keeps the action log's link
        assert org is not None and org.name == "Purged shop" and org.active is False and org.offboarded_at
        for col in ("company_email", "company_phone", "company_address", "owner_id", "subdomain", "logo_data",
                    "stripe_customer_id", "admin_notes", "website", "company_details"):
            assert getattr(org, col) is None, col
        row = (await db.execute(select(AdminActionLog).where(
            AdminActionLog.org_id == a["org"], AdminActionLog.action == "purge_shop"))).scalar_one()
        assert row.outcome == "allowed" and row.after["counts"] == dry["counts"] and row.reason
        assert row.before["name"] == "Leaving Co"
        assert await admin_log.verify_chain(db)
        # Earlier action-log rows about A survive (append-only), pointing at the tombstone.
        assert (await db.execute(select(func.count()).select_from(AdminActionLog).where(
            AdminActionLog.org_id == a["org"]))).scalar() >= 2

    # Purged once: the tombstone can't be purged again, and the detail page says so.
    again = await client.post(f"/api/admin/tenants/{a['org']}/purge",
                              json={"confirm_name": "Purged shop", "reason": "again"}, headers=H)
    assert again.status_code == 409
    detail = (await client.get(f"/api/admin/tenants/{a['org']}", headers=H)).json()
    assert detail["purge"]["purged_at"] and detail["purge"]["eligible"] is False


async def test_a_file_object_another_shop_still_points_at_is_kept(client, _local_media):
    """Content-addressed storage: if a surviving shop's blob shares A's object key, the object stays."""
    a = await _shop(client, "Leaving Co")
    b = await _shop(client, "Staying Co")
    async with AsyncSessionLocal() as db:
        a_blob = await db.get(MediaBlob, a["blob"])
        shared = MediaBlob(org_id=b["org"], backend="local", storage_key=a_blob.storage_key, mime="image/png",
                           name="copy.png", size_bytes=a_blob.size_bytes)
        db.add(shared)
        await db.commit()
        shared_id = shared.id
    await _offboard(a["org"], 31)
    H, _ = await admin_headers(client)
    dry = (await client.get(f"/api/admin/tenants/{a['org']}/purge", headers=H)).json()
    assert dry["media_files"] == 0                  # the one object is still in use
    assert (await _purge(client, H, a)).status_code == 200
    assert await _blob_bytes(shared_id) == base64.b64decode(PNG)
    assert await _blob_bytes(b["blob"]) == base64.b64decode(PNG)


async def test_a_row_of_another_shop_pointing_into_the_shop_blocks_the_purge(client):
    """Nothing may cascade into (or dangle from) another shop: a cross-shop link found before deleting
    refuses the whole purge and deletes nothing."""
    a = await _shop(client, "Leaving Co")
    b = await _shop(client, "Staying Co")
    await _offboard(a["org"], 31)
    async with AsyncSessionLocal() as db:
        # A Core insert skips the ORM tenant guard, as a bad import or hand-written SQL could. This
        # link is ON DELETE CASCADE: deleting A's client would silently delete B's row.
        await db.execute(insert(ClientPortalLink.__table__).values(
            org_id=b["org"], client_id=a["client"], token_hash=uuid.uuid4().hex,
            expires_at=datetime.utcnow() + timedelta(days=30)))
        await db.commit()
    a_before, b_before = await _counts(a["org"]), await _counts(b["org"])
    H, _ = await admin_headers(client)
    dry = (await client.get(f"/api/admin/tenants/{a['org']}/purge", headers=H)).json()
    assert dry["eligible"] is False and "client_portal_links" in dry["why_not"]
    r = await _purge(client, H, a)
    assert r.status_code == 409 and "client_portal_links" in r.json()["detail"]
    a_after = await _counts(a["org"])
    assert a_after.pop("admin_action_log") == a_before.pop("admin_action_log") + 1   # the refusal, logged
    assert a_after == a_before and await _counts(b["org"]) == b_before


# ── never automatic ────────────────────────────────────────────────────────────────────────────
def test_no_scheduled_job_purges_shops():
    import backend.core.scheduled  # noqa: F401 — registers the periodic jobs
    import backend.routers.sso  # noqa: F401
    from backend.core.jobs import job_runner
    assert job_runner._periodics
    for p in job_runner._periodics:
        src = inspect.getsource(p.fn)
        assert "shop_purge" not in src and "purge_shop" not in src, p.name
    # Only the admin router (and this test) reach the purge service.
    root = pathlib.Path(__file__).resolve().parents[2]
    uses = re.compile(r"import[^\n]*\bshop_purge\b|\bshop_purge\.\w+\(")
    users = sorted(str(f.relative_to(root)) for f in list((root / "backend").rglob("*.py")) + [root / "main.py"]
                   if uses.search(f.read_text()) and f.name not in ("shop_purge.py", "test_admin_shop_purge.py"))
    assert users == ["backend/routers/admin.py"]
