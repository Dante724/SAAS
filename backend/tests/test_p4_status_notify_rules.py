"""Phase 4 — the notification rules are the one source of truth for customer status messages.

OrderStatus.triggers_notification used to drive a legacy template dispatch that only ran when
NO rule matched, so with the seeded "email on every status change" rule it was nearly always
ignored. The fallback is gone; flagged statuses are converted into the per-status rules the
fallback would have produced (services/legacy_status_notifs). These tests pin that sending is
unchanged for (a) a seeded default shop and (b) a shop that only relied on the status flag.
"""
import pytest
from sqlalchemy import delete, select

from backend.models import NotificationLog, NotificationRule, NotificationTemplate, OrderStatus
from backend.services import legacy_status_notifs as legacy


async def _org(client, h):
    return (await client.get("/api/auth/me", headers=h)).json()["org_id"]


async def _statuses(client, h):
    return {s["key"]: s for s in (await client.get("/api/statuses", headers=h)).json()["items"]}


async def _order(client, h):
    loc = (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]
    cid = (await client.post("/api/clients", json={"name": "Sam", "phone": "+15550101", "email": "sam@example.com"},
                             headers=h)).json()["id"]
    return (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x"},
                              headers=h)).json()["id"]


async def _move(client, h, oid, key):
    r = await client.patch(f"/api/orders/{oid}/status", json={"status": key}, headers=h)
    assert r.status_code == 200, r.text


async def _logs(db, oid, event):
    db.expire_all()
    return (await db.execute(select(NotificationLog).where(
        NotificationLog.order_id == oid, NotificationLog.trigger_event == event))).scalars().all()


# ── planner (pure) ──────────────────────────────────────────────────────────────────────────
_TPL = {"id": "t1", "channel": "sms", "trigger_event": "status_change", "trigger_status": None, "active": True}


def test_plan_converts_flag_to_per_status_rule_per_template():
    st = [{"key": "ready", "record_type": "work_order", "is_terminal": False},
          {"key": "done", "record_type": "work_order", "is_terminal": True}]
    scoped = {**_TPL, "id": "t2", "channel": "email", "trigger_status": "ready"}
    out = legacy.plan(st, [], [_TPL, scoped])
    got = sorted((r["trigger_status_key"], r["event"], r["channel"], r["template_id"]) for r in out)
    assert got == [("done", "closed", "sms", "t1"), ("ready", "status_changed", "email", "t2"),
                   ("ready", "status_changed", "sms", "t1")]
    assert all(r["active"] and r["record_type"] == "work_order" for r in out)


def test_plan_skips_when_a_rule_already_fires_or_nothing_to_send():
    st = [{"key": "ready", "record_type": "work_order", "is_terminal": False}]
    catch_all = {"module": "order", "scope": "client", "event": "status_changed", "trigger_status_key": None,
                 "record_type": None, "active": True}
    assert legacy.plan(st, [catch_all], [_TPL]) == []                       # fallback never ran
    assert legacy.plan(st, [{**catch_all, "active": False}], [_TPL])        # an off rule doesn't cover
    assert legacy.plan(st, [{**catch_all, "record_type": "estimate"}], [_TPL])  # other record type
    assert legacy.plan(st, [{**catch_all, "event": "closed"}], [_TPL])      # closing rule ≠ this event
    assert legacy.plan(st, [], []) == []                                    # no template → sent nothing
    assert legacy.plan(st, [], [{**_TPL, "trigger_status": "other"}]) == []


# ── (a) seeded default shop: unchanged ───────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_seeded_shop_sends_one_email_per_status_change(client, tenant, db):
    org = await _org(client, tenant)
    await _statuses(client, tenant)
    # An old-style flag plus a template, as a pre-phase-4 shop could have: covered by the
    # catch-all rule, so it converts to nothing (the fallback never fired for them).
    db.add(NotificationTemplate(org_id=org, channel="sms", trigger_event="status_change",
                                body_template="legacy text", active=True))
    st = (await db.execute(select(OrderStatus).where(OrderStatus.org_id == org,
                                                     OrderStatus.key == "diagnosed"))).scalar_one()
    st.triggers_notification = True
    await db.commit()
    before = len((await db.execute(select(NotificationRule).where(NotificationRule.org_id == org))).scalars().all())
    assert await legacy.convert(db, org) == 0
    await db.commit()
    after = len((await db.execute(select(NotificationRule).where(NotificationRule.org_id == org))).scalars().all())
    assert after == before
    flags = (await db.execute(select(OrderStatus.triggers_notification).where(OrderStatus.org_id == org))).scalars().all()
    assert not any(flags)

    oid = await _order(client, tenant)
    await _move(client, tenant, oid, "diagnosed")
    logs = await _logs(db, oid, "status_changed")
    assert [(lg.channel, lg.status) for lg in logs] == [("email", "sent")]
    await _move(client, tenant, oid, "done")
    closed = await _logs(db, oid, "closed")
    assert [lg.channel for lg in closed] == ["email"]
    assert not await _logs(db, oid, "status_change")       # the legacy path wrote these


# ── (b) a shop that only used the status switch: unchanged ───────────────────────────────────
@pytest.mark.asyncio
async def test_flag_only_shop_keeps_its_text_after_conversion(client, tenant, db):
    org = await _org(client, tenant)
    await _statuses(client, tenant)
    await db.execute(delete(NotificationRule).where(NotificationRule.org_id == org))
    tpl = NotificationTemplate(org_id=org, channel="sms", trigger_event="status_change",
                               body_template="Your job is ready", active=True)
    db.add(tpl)
    for key in ("ready", "done"):
        st = (await db.execute(select(OrderStatus).where(OrderStatus.org_id == org,
                                                         OrderStatus.key == key))).scalar_one()
        st.triggers_notification = True
    await db.commit()

    assert await legacy.convert(db, org) == 2
    await db.commit()
    assert await legacy.convert(db, org) == 0               # idempotent
    rules = (await db.execute(select(NotificationRule).where(NotificationRule.org_id == org))).scalars().all()
    assert sorted((r.trigger_status_key, r.event, r.channel, r.template_id == tpl.id) for r in rules) == [
        ("done", "closed", "sms", True), ("ready", "status_changed", "sms", True)]

    oid = await _order(client, tenant)
    await _move(client, tenant, oid, "diagnosed")           # not flagged → nothing, as before
    assert not await _logs(db, oid, "status_changed")
    await _move(client, tenant, oid, "ready")
    logs = await _logs(db, oid, "status_changed")
    # First text to this customer also carries the opt-out line.
    assert [(lg.channel, lg.body) for lg in logs] == [("sms", "Your job is ready\nReply STOP to opt out.")]
    await _move(client, tenant, oid, "done")
    assert [lg.channel for lg in await _logs(db, oid, "closed")] == ["sms"]


@pytest.mark.asyncio
async def test_no_legacy_fallback_when_the_rule_is_turned_off(client, tenant, db):
    """Turning the per-status rule off in Settings › Notifications really stops the message."""
    org = await _org(client, tenant)
    await _statuses(client, tenant)
    await db.execute(delete(NotificationRule).where(NotificationRule.org_id == org))
    db.add(NotificationTemplate(org_id=org, channel="sms", trigger_event="status_change",
                                body_template="hi", active=True))
    await db.commit()
    oid = await _order(client, tenant)
    await _move(client, tenant, oid, "ready")
    assert not await _logs(db, oid, "status_changed") and not await _logs(db, oid, "status_change")


# ── status API ───────────────────────────────────────────────────────────────────────────────
_FIELDS = ("name", "status_group", "color", "is_default", "triggers_invoice", "triggers_payment",
           "locks_edits", "time_limit_min", "moves_asset_to", "converts_to_order", "shows_accept",
           "require_comment", "require_payment", "require_checklist", "record_type")


@pytest.mark.asyncio
async def test_old_client_flag_becomes_a_rule_and_stage_flip_keeps_rules_firing(client, tenant, db):
    org = await _org(client, tenant)
    st = (await _statuses(client, tenant))["waiting_parts"]
    assert st["triggers_notification"] is False              # no longer seeded on
    await db.execute(delete(NotificationRule).where(NotificationRule.org_id == org))
    db.add(NotificationTemplate(org_id=org, channel="email", trigger_event="status_change",
                                subject="s", body_template="b", active=True))
    await db.commit()

    body = {k: st[k] for k in _FIELDS}
    r = await client.put(f"/api/statuses/{st['id']}", json={**body, "triggers_notification": True}, headers=tenant)
    assert r.status_code == 200 and r.json()["triggers_notification"] is False
    db.expire_all()
    rules = (await db.execute(select(NotificationRule).where(NotificationRule.org_id == org))).scalars().all()
    assert [(x.trigger_status_key, x.event, x.channel) for x in rules] == [("waiting_parts", "status_changed", "email")]

    r = await client.put(f"/api/statuses/{st['id']}", json={**body, "status_group": "closed"}, headers=tenant)
    assert r.status_code == 200
    db.expire_all()
    assert (await db.execute(select(NotificationRule.event).where(
        NotificationRule.org_id == org))).scalars().all() == ["closed"]


# ── Alembic data step ────────────────────────────────────────────────────────────────────────
def test_migration_converts_and_clears_flags(tmp_path):
    import importlib.util
    from pathlib import Path

    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext
    from sqlalchemy import create_engine, text

    from backend.db.database import Base

    eng = create_engine(f"sqlite:///{tmp_path / 'm.db'}")
    Base.metadata.create_all(eng)
    from sqlalchemy.orm import Session

    from backend.models import Organisation
    with Session(eng) as ses:                       # ORM inserts so column defaults apply
        org = Organisation(name="Shop")
        ses.add(org)
        ses.flush()
        for k, flag in (("ready", True), ("new", False)):
            ses.add(OrderStatus(org_id=org.id, key=k, name=k, status_group="ready", color="#000",
                                record_type="work_order", triggers_notification=flag))
        ses.add(NotificationTemplate(id="t1", org_id=org.id, channel="sms", trigger_event="status_change",
                                     body_template="hi", active=True))
        ses.commit()
    path = Path(__file__).resolve().parents[1] / "migrations" / "versions" / "nt144p4_status_notify_to_rules.py"
    spec = importlib.util.spec_from_file_location("nt144p4", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    with eng.begin() as c:
        with Operations.context(MigrationContext.configure(c)):
            mod.upgrade()
    with eng.connect() as c:
        rows = c.execute(text("SELECT trigger_status_key, event, channel, template_id, active "
                              "FROM notification_rules")).all()
        assert [tuple(r) for r in rows] == [("ready", "status_changed", "sms", "t1", 1)]
        assert c.execute(text("SELECT COUNT(*) FROM order_statuses WHERE triggers_notification = 1")).scalar() == 0


# ── a status's own message replaces the every-status one on that channel ──────────────────────
def test_status_overrides_is_per_channel():
    from types import SimpleNamespace as R

    from backend.services.notification_rules import _status_overrides
    every_email, every_sms = R(channel="email", trigger_status_key=None), R(channel="sms", trigger_status_key=None)
    own_email = R(channel="email", trigger_status_key="ready")
    assert _status_overrides([every_email, every_sms, own_email]) == [every_sms, own_email]
    assert _status_overrides([every_email, every_sms]) == [every_email, every_sms]


@pytest.mark.asyncio
async def test_own_status_message_replaces_every_status_message(client, tenant, db):
    org = await _org(client, tenant)
    await _statuses(client, tenant)
    db.add(NotificationRule(org_id=org, module="order", scope="client", event="status_changed",
                            trigger_status_key="ready", channel="email", active=True))
    await db.commit()
    oid = await _order(client, tenant)
    await _move(client, tenant, oid, "ready")              # own email only, not the seeded one too
    logs = await _logs(db, oid, "status_changed")
    own = (await db.execute(select(NotificationRule.id).where(
        NotificationRule.org_id == org, NotificationRule.trigger_status_key == "ready"))).scalar_one()
    assert [(lg.channel, lg.rule_id) for lg in logs] == [("email", own)]
    await _move(client, tenant, oid, "diagnosed")          # other statuses keep the every-status email
    logs = await _logs(db, oid, "status_changed")
    # _logs has no order (Postgres returns rows in any order): "ready" sent the own rule once,
    # "diagnosed" sent a different one.
    assert len(logs) == 2 and [lg.rule_id for lg in logs].count(own) == 1
