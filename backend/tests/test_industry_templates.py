"""Industry templates — Phase 1 (data model + registry + seed).

Proves the isolation guarantees from the spec at the data layer. Endpoint- and
UI-level behaviour is covered in later phases.
"""
import uuid

import pytest
from sqlalchemy import func, select

from backend.models import (
    AssetBrand,
    AssetGroup,
    AssetIdentifierRule,
    AssetModel,
    Device,
    Order,
    Organisation,
)
from backend.services import industry as I


# ── registry (no DB) ─────────────────────────────────────────────────────────
def test_ten_templates_present():
    assert I.TEMPLATE_KEYS == [
        "mobile_phone", "computer", "tools", "small_engine",
        "powersports", "auto", "bike", "marine", "fleet_truck", "general",
    ]


def test_fleet_truck_uses_unit_noun_and_vin():
    d = I.describe("fleet_truck")
    assert d["asset_label"] == "Unit" and d["asset_label_plural"] == "Units"
    assert I.identifier_rule("fleet_truck")["primary"] == "vin"
    assert "unit_number" in [f["key"] for f in I.field_schema("fleet_truck")]


def test_phone_shows_imei_not_vin_or_hin():
    keys = [f["key"] for f in I.field_schema("mobile_phone")]
    assert "imei" in keys
    assert "vin" not in keys and "hin" not in keys
    assert I.identifier_rule("mobile_phone")["primary"] == "imei"


def test_auto_shows_vin_not_imei():
    keys = [f["key"] for f in I.field_schema("auto")]
    assert "vin" in keys
    assert "imei" not in keys
    assert I.identifier_rule("auto")["primary"] == "vin"


def test_marine_shows_hin():
    keys = [f["key"] for f in I.field_schema("marine")]
    assert "hin" in keys
    assert I.identifier_rule("marine")["primary"] == "hin"
    # marine is the only template whose default primary is HIN
    hin_templates = [k for k in I.TEMPLATE_KEYS if I.identifier_rule(k)["primary"] == "hin"]
    assert hin_templates == ["marine"]


def test_group_identifier_overrides():
    # a phone is IMEI but a tablet in the same template is serial
    assert I.identifier_rule("mobile_phone", "phone")["primary"] == "imei"
    assert I.identifier_rule("mobile_phone", "tablet")["primary"] == "serial"
    # a motorcycle uses VIN, a bicycle uses serial — same 'bike' template
    assert I.identifier_rule("bike", "motorcycle")["primary"] == "vin"
    assert I.identifier_rule("bike", "bicycle")["primary"] == "serial"
    # marine boat -> HIN, trailer -> VIN
    assert I.identifier_rule("marine", "boat")["primary"] == "hin"
    assert I.identifier_rule("marine", "trailer")["primary"] == "vin"


def test_wo_button_comes_from_template():
    assert I.wo_button("mobile_phone") == "+ Ticket"
    assert I.wo_button("computer") == "+ Ticket"
    assert I.wo_button("auto") == "+ Order"
    assert I.wo_button("marine") == "+ Order"
    assert I.wo_button("general") == "+ Order"
    # only phone/computer use the "Ticket" wording; everything else is "+ Order"
    tickets = [k for k in I.TEMPLATE_KEYS if I.wo_button(k) == "+ Ticket"]
    assert set(tickets) == {"mobile_phone", "computer"}


def test_legacy_alias_mapping():
    assert I.normalize_template_key("generic") == "general"
    assert I.normalize_template_key("device_repair") == "mobile_phone"
    assert I.normalize_template_key("auto_repair") == "auto"
    assert I.normalize_template_key("tool_repair") == "tools"
    assert I.normalize_template_key(None) == "general"
    assert I.normalize_template_key("nonsense") == "general"
    # canonical keys pass through unchanged
    for k in I.TEMPLATE_KEYS:
        assert I.normalize_template_key(k) == k


def test_bike_serial_vin_field_resolves_by_group():
    f = next(x for x in I.field_schema("bike", "motorcycle") if x["key"] == "serial_vin")
    assert f["identifier_kind"] == "vin" and f["label"] == "VIN"
    f2 = next(x for x in I.field_schema("bike", "bicycle") if x["key"] == "serial_vin")
    assert f2["identifier_kind"] == "serial"


# ── seed + isolation (DB) ────────────────────────────────────────────────────
async def _org(db) -> str:
    oid = f"org-{uuid.uuid4().hex[:8]}"
    db.add(Organisation(id=oid, name="Seed Test"))
    await db.commit()
    return oid


async def test_seed_materializes_and_is_idempotent(db):
    oid = await _org(db)
    first = await I.ensure_industry_seed(db, oid)
    assert first["groups"] > 0 and first["rules"] > 0 and first["brands"] > 0
    again = await I.ensure_industry_seed(db, oid)
    assert again == {"groups": 0, "rules": 0, "brands": 0, "models": 0}


async def test_same_brand_name_is_separate_per_template(db):
    oid = await _org(db)
    await I.ensure_industry_seed(db, oid)
    rows = (await db.execute(select(AssetBrand.template_key).where(
        AssetBrand.org_id == oid, AssetBrand.name == "Yamaha"))).scalars().all()
    # Yamaha is seeded under multiple templates as DISTINCT rows
    assert set(rows) >= {"powersports", "marine", "small_engine", "bike"}
    # they are different rows, not one shared row
    assert len(rows) == len(set(rows))


async def test_groups_never_cross_templates(db):
    oid = await _org(db)
    await I.ensure_industry_seed(db, oid)
    auto = set((await db.execute(select(AssetGroup.key).where(
        AssetGroup.org_id == oid, AssetGroup.template_key == "auto"))).scalars().all())
    phone = set((await db.execute(select(AssetGroup.key).where(
        AssetGroup.org_id == oid, AssetGroup.template_key == "mobile_phone"))).scalars().all())
    assert auto == {"car", "truck", "suv", "van"}
    assert phone == {"phone", "tablet", "smartwatch"}
    assert auto.isdisjoint(phone)


async def test_identifier_rules_seeded_per_template(db):
    oid = await _org(db)
    await I.ensure_industry_seed(db, oid)

    async def primary(tk):
        return (await db.execute(select(AssetIdentifierRule.primary_kind).where(
            AssetIdentifierRule.org_id == oid, AssetIdentifierRule.template_key == tk,
            AssetIdentifierRule.asset_group_id.is_(None)))).scalar_one()

    assert await primary("mobile_phone") == "imei"
    assert await primary("auto") == "vin"
    assert await primary("marine") == "hin"


async def test_seed_is_org_scoped(db):
    a = await _org(db)
    b = await _org(db)
    await I.ensure_industry_seed(db, a)
    # org b has nothing until seeded
    n_b = (await db.execute(select(func.count()).select_from(AssetGroup).where(
        AssetGroup.org_id == b))).scalar()
    assert n_b == 0
    n_a = (await db.execute(select(func.count()).select_from(AssetGroup).where(
        AssetGroup.org_id == a))).scalar()
    assert n_a > 0


async def test_brand_model_chain(db):
    oid = await _org(db)
    await I.ensure_industry_seed(db, oid)
    apple = (await db.execute(select(AssetBrand).where(
        AssetBrand.org_id == oid, AssetBrand.template_key == "mobile_phone",
        AssetBrand.name == "Apple"))).scalar_one()
    models = (await db.execute(select(AssetModel.name).where(
        AssetModel.org_id == oid, AssetModel.brand_id == apple.id))).scalars().all()
    assert "iPhone 14 Pro" in models
    # those models are tagged with the phone template, never leaking elsewhere
    leaked = (await db.execute(select(func.count()).select_from(AssetModel).where(
        AssetModel.org_id == oid, AssetModel.brand_id == apple.id,
        AssetModel.template_key != "mobile_phone"))).scalar()
    assert leaked == 0


# ── legacy-data safety (new columns are nullable) ────────────────────────────
async def test_new_columns_default_null_for_legacy_records(db):
    d = Device(owner_type="company", asset_type="Device")
    db.add(d)
    await db.commit()
    await db.refresh(d)
    # a device created without a template keeps null (renders via legacy fallback)
    assert d.template_key is None
    assert d.asset_group_id is None
