"""Industry templates — the code-level source of truth for the per-industry
asset system.

Each template defines: a human label, the noun used for an asset (Device /
Vehicle / Vessel / …), the work-order button/label, its default asset GROUPS,
an ordered FIELD schema, and the identifier/scan rule (which is template- and
group-aware so a phone never shows VIN and a car never shows IMEI).

The org-editable data (groups, brands, models, identifier rules) is materialised
into the asset_groups / asset_brands / asset_models / asset_identifier_rules
tables by ``ensure_industry_seed``. This module is the authoritative default;
the tables let each shop extend/curate it without ever mixing templates.

Design guarantees (see the product spec):
  * directories are always scoped by (org_id, template_key) — never shared;
  * "Yamaha" under powersports, marine, small_engine and bike are distinct rows;
  * VIN never appears for phones; IMEI never for cars; HIN only for marine.

Phase 1 is additive: nothing here changes runtime behaviour yet. It is wired
into settings, the work-order flow and scanning in later phases.
"""
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# ── Identifier kinds ─────────────────────────────────────────────────────────
IDENTIFIER_LABELS = {
    "imei": "IMEI",
    "vin": "VIN",
    "hin": "HIN",
    "serial": "Serial number",
    "engine_serial": "Engine serial",
    "asset_tag": "Asset tag",
    "barcode": "Barcode",
}

# ── Field library ────────────────────────────────────────────────────────────
# key -> {label, type, [identifier_kind]}. Field types the frontend can render:
# text | number | bool | password | select | identifier.
FIELD_LIBRARY: dict[str, dict] = {
    "brand":            {"label": "Brand", "type": "text"},
    "model":            {"label": "Model", "type": "text"},
    "make":             {"label": "Make", "type": "text"},
    "year":             {"label": "Year", "type": "number"},
    "color":            {"label": "Colour", "type": "text"},
    "storage":          {"label": "Storage", "type": "text"},
    "carrier":          {"label": "Carrier", "type": "text"},
    "passcode":         {"label": "Passcode", "type": "password"},
    "password":         {"label": "Password", "type": "password"},
    "condition":        {"label": "Condition", "type": "text"},
    "cpu":              {"label": "CPU", "type": "text"},
    "ram":              {"label": "RAM", "type": "text"},
    "os":               {"label": "Operating system", "type": "text"},
    "charger_received": {"label": "Charger received", "type": "bool"},
    "voltage":          {"label": "Voltage", "type": "text"},
    "fuel_type":        {"label": "Fuel type", "type": "text"},
    "warranty_status":  {"label": "Warranty status", "type": "text"},
    "engine_model":     {"label": "Engine model", "type": "text"},
    "engine_size":      {"label": "Engine size", "type": "text"},
    "engine":           {"label": "Engine", "type": "text"},
    "transmission":     {"label": "Transmission", "type": "text"},
    "trim":             {"label": "Trim", "type": "text"},
    "hours":            {"label": "Hours", "type": "number"},
    "odometer_hours":   {"label": "Odometer / hours", "type": "number"},
    "mileage":          {"label": "Mileage", "type": "number"},
    "plate":            {"label": "Plate", "type": "text"},
    "plate_registration": {"label": "Plate / registration", "type": "text"},
    "registration":     {"label": "Registration", "type": "text"},
    "frame_size":       {"label": "Frame size", "type": "text"},
    "battery_serial":   {"label": "Battery serial", "type": "text"},
    "length":           {"label": "Length", "type": "number"},
    "unit_number":      {"label": "Unit number", "type": "text"},
    "dot_number":       {"label": "DOT number", "type": "text"},
    "gvwr":             {"label": "GVWR", "type": "text"},
    # identifier-typed fields (carry a scan kind)
    "imei":             {"label": "IMEI", "type": "identifier", "identifier_kind": "imei"},
    "serial":           {"label": "Serial number", "type": "identifier", "identifier_kind": "serial"},
    "vin":              {"label": "VIN", "type": "identifier", "identifier_kind": "vin"},
    "hin":              {"label": "HIN", "type": "identifier", "identifier_kind": "hin"},
    "engine_serial":    {"label": "Engine serial", "type": "identifier", "identifier_kind": "engine_serial"},
    "asset_tag":        {"label": "Asset tag", "type": "identifier", "identifier_kind": "asset_tag"},
    # identifier fields whose concrete kind depends on the group (resolved via
    # the group identifier rule); default label reflects the mixed case
    "serial_vin":       {"label": "Serial / VIN", "type": "identifier", "identifier_kind": "serial"},
    "vin_serial":       {"label": "VIN / Serial", "type": "identifier", "identifier_kind": "serial"},
}


def _g(key: str, label: str) -> dict:
    return {"key": key, "label": label}


# ── Templates ────────────────────────────────────────────────────────────────
# ``groups`` items may carry an optional per-group identifier override.
TEMPLATES: dict[str, dict] = {
    "mobile_phone": {
        "label": "Mobile / Phone Repair",
        "asset_label": "Device", "asset_label_plural": "Devices",
        "wo_button": "+ Ticket", "wo_title": "Create Phone Repair Ticket", "wo_noun": "ticket",
        "fields": ["brand", "model", "imei", "serial", "color", "storage", "carrier", "passcode", "condition"],
        "identifier": {"primary": "imei", "allowed": ["imei", "serial", "barcode"]},
        "groups": [
            {**_g("phone", "Phone")},
            {**_g("tablet", "Tablet"), "identifier": {"primary": "serial", "allowed": ["serial", "barcode"]}},
            {**_g("smartwatch", "Smartwatch"), "identifier": {"primary": "serial", "allowed": ["serial", "barcode"]}},
        ],
    },
    "computer": {
        "label": "Computer / Laptop Repair",
        "asset_label": "Computer", "asset_label_plural": "Computers",
        "wo_button": "+ Ticket", "wo_title": "Create Computer Repair Ticket", "wo_noun": "ticket",
        "fields": ["brand", "model", "serial", "cpu", "ram", "storage", "os", "password", "charger_received"],
        "identifier": {"primary": "serial", "allowed": ["serial", "asset_tag", "barcode"]},
        "groups": [
            _g("laptop", "Laptop"), _g("desktop", "Desktop"), _g("all_in_one", "All-in-One"),
            _g("monitor", "Monitor"), _g("printer", "Printer"),
        ],
    },
    "tools": {
        "label": "Tools Repair",
        "asset_label": "Tool", "asset_label_plural": "Tools",
        "wo_button": "+ Order", "wo_title": "Create Work Order", "wo_noun": "order",
        "fields": ["brand", "model", "serial", "voltage", "fuel_type", "warranty_status"],
        "identifier": {"primary": "serial", "allowed": ["serial", "barcode"]},
        "groups": [
            _g("power_tool", "Power Tool"), _g("hand_tool", "Hand Tool"), _g("battery", "Battery"),
            _g("charger", "Charger"), _g("compressor", "Compressor"),
        ],
    },
    "small_engine": {
        "label": "Small Engine Repair",
        "asset_label": "Equipment", "asset_label_plural": "Equipment",
        "wo_button": "+ Order", "wo_title": "Create Work Order", "wo_noun": "order",
        "fields": ["brand", "model", "serial", "engine_model", "fuel_type", "hours", "vin_serial"],
        "identifier": {"primary": "serial", "allowed": ["serial", "engine_serial"]},
        "groups": [
            _g("lawn_mower", "Lawn Mower"), _g("generator", "Generator"), _g("pressure_washer", "Pressure Washer"),
            _g("snow_blower", "Snow Blower"), _g("chainsaw", "Chainsaw"),
        ],
    },
    "powersports": {
        "label": "Powersports Repair",
        "asset_label": "Vehicle", "asset_label_plural": "Vehicles",
        "wo_button": "+ Order", "wo_title": "Create Work Order", "wo_noun": "order",
        "fields": ["vin", "brand", "model", "year", "engine_size", "odometer_hours", "plate_registration"],
        "identifier": {"primary": "vin", "allowed": ["vin", "serial"]},
        "groups": [
            _g("atv", "ATV"), _g("utv", "UTV"), _g("dirt_bike", "Dirt Bike"),
            _g("snowmobile", "Snowmobile"),
            {**_g("jet_ski", "Jet Ski"), "identifier": {"primary": "hin", "allowed": ["hin", "vin", "serial"]}},
        ],
    },
    "auto": {
        "label": "Car / Auto Repair",
        "asset_label": "Vehicle", "asset_label_plural": "Vehicles",
        "wo_button": "+ Order", "wo_title": "Create Work Order", "wo_noun": "order",
        "fields": ["vin", "make", "model", "year", "trim", "plate", "mileage", "engine", "transmission"],
        "identifier": {"primary": "vin", "allowed": ["vin"]},
        "groups": [
            _g("car", "Car"), _g("truck", "Truck"), _g("suv", "SUV"), _g("van", "Van"),
        ],
    },
    "bike": {
        "label": "Bike / Bicycle / Motorcycle Repair",
        "asset_label": "Bike", "asset_label_plural": "Bikes",
        "wo_button": "+ Order", "wo_title": "Create Work Order", "wo_noun": "order",
        "fields": ["brand", "model", "serial_vin", "frame_size", "battery_serial", "mileage"],
        "identifier": {"primary": "serial", "allowed": ["serial", "vin"]},
        "groups": [
            {**_g("bicycle", "Bicycle"), "identifier": {"primary": "serial", "allowed": ["serial"]}},
            {**_g("e_bike", "E-Bike"), "identifier": {"primary": "serial", "allowed": ["serial"]}},
            {**_g("motorcycle", "Motorcycle"), "identifier": {"primary": "vin", "allowed": ["vin", "serial"]}},
            {**_g("scooter", "Scooter"), "identifier": {"primary": "vin", "allowed": ["vin", "serial"]}},
        ],
    },
    "marine": {
        "label": "Marine / Vessel Repair",
        "asset_label": "Vessel", "asset_label_plural": "Vessels",
        "wo_button": "+ Order", "wo_title": "Create Work Order", "wo_noun": "order",
        "fields": ["hin", "vin", "engine_serial", "make", "model", "year", "length", "registration"],
        "identifier": {"primary": "hin", "allowed": ["hin", "vin", "engine_serial"]},
        "groups": [
            {**_g("boat", "Boat"), "identifier": {"primary": "hin", "allowed": ["hin", "engine_serial"]}},
            {**_g("jet_ski", "Jet Ski"), "identifier": {"primary": "hin", "allowed": ["hin", "vin"]}},
            {**_g("outboard_motor", "Outboard Motor"), "identifier": {"primary": "engine_serial", "allowed": ["engine_serial", "serial"]}},
            {**_g("trailer", "Trailer"), "identifier": {"primary": "vin", "allowed": ["vin", "serial"]}},
        ],
    },
    "fleet_truck": {
        "label": "Fleet & Truck Repair",
        "asset_label": "Unit", "asset_label_plural": "Units",
        "wo_button": "+ Order", "wo_title": "Create Work Order", "wo_noun": "order",
        "fields": ["vin", "unit_number", "make", "model", "year", "plate", "mileage",
                   "engine", "transmission", "dot_number", "gvwr"],
        "identifier": {"primary": "vin", "allowed": ["vin", "serial", "asset_tag"]},
        "groups": [
            _g("truck", "Truck"), _g("tractor", "Tractor"),
            {**_g("trailer", "Trailer"), "identifier": {"primary": "vin", "allowed": ["vin", "serial"]}},
            {**_g("reefer", "Reefer"), "identifier": {"primary": "serial", "allowed": ["serial", "vin"]}},
            _g("van", "Van"), _g("bus", "Bus"),
        ],
    },
    "general": {
        "label": "General Repair",
        "asset_label": "Asset", "asset_label_plural": "Assets",
        "wo_button": "+ Order", "wo_title": "Create Work Order", "wo_noun": "order",
        "fields": ["brand", "model", "serial", "condition"],
        "identifier": {"primary": "serial", "allowed": ["serial", "barcode"]},
        "groups": [
            _g("device", "Device"), _g("equipment", "Equipment"), _g("other", "Other"),
        ],
    },
}

TEMPLATE_KEYS = list(TEMPLATES.keys())
DEFAULT_TEMPLATE = "general"

# Legacy Organisation.industry_template values -> canonical new keys, so existing
# orgs keep working before the new Settings picker is wired (Phase 2/3).
LEGACY_ALIASES = {
    "generic": "general",
    "device_repair": "mobile_phone",
    "auto_repair": "auto",
    "tool_repair": "tools",
    "appliance_repair": "general",
    # already-canonical values map to themselves
    "powersports": "powersports",
    "marine": "marine",
    "small_engine": "small_engine",
}

# Starter brand/model data (system rows), focused on what US/CA shops see every day so the
# brand/model pickers are useful on day one. Shops add anything else just by typing it at intake
# (services/asset_catalog.py remembers it). The same brand recurs across templates as separate rows.
_PHONE_BRANDS = ["Apple", "Samsung", "Google", "Motorola", "OnePlus", "LG", "TCL", "Nokia", "Xiaomi"]
_COMPUTER_BRANDS = ["Apple", "Dell", "HP", "Lenovo", "Asus", "Acer", "Microsoft", "MSI", "Razer", "Samsung"]
_CONSOLE_BRANDS = ["Sony", "Microsoft", "Nintendo", "Valve"]
STARTER_BRANDS: dict[str, list[str]] = {
    "mobile_phone": _PHONE_BRANDS,
    "computer":     _COMPUTER_BRANDS,
    "tools":        ["DeWalt", "Makita", "Milwaukee", "Bosch", "Ryobi", "Ridgid", "Craftsman", "Black+Decker", "Metabo HPT", "Hilti"],
    "small_engine": ["Honda", "Briggs & Stratton", "Kohler", "Kawasaki", "Yamaha", "Toro", "Husqvarna", "Stihl", "Echo", "John Deere", "Cub Cadet", "Troy-Bilt", "Generac"],
    "powersports":  ["Polaris", "Can-Am", "Honda", "Yamaha", "Kawasaki", "Suzuki", "Arctic Cat", "Ski-Doo", "Sea-Doo", "Harley-Davidson"],
    "auto":         ["Ford", "Chevrolet", "Toyota", "Honda", "Nissan", "Jeep", "Ram", "GMC", "Hyundai", "Kia", "Subaru", "Volkswagen", "BMW", "Mercedes-Benz", "Tesla"],
    "bike":         ["Trek", "Specialized", "Giant", "Yamaha", "Cannondale", "Santa Cruz", "Rad Power Bikes", "Aventon", "Brompton"],
    "marine":       ["Mercury", "Yamaha", "Suzuki", "Evinrude", "Honda Marine", "Boston Whaler", "Sea Ray", "Bayliner"],
    "fleet_truck":  ["Freightliner", "Peterbilt", "Kenworth", "International", "Volvo", "Mack", "Isuzu", "Hino"],
    "general":      sorted(set(_PHONE_BRANDS + _COMPUTER_BRANDS + _CONSOLE_BRANDS), key=str.lower),
}

_PHONE_MODELS = {
    "Apple": ["iPhone 16 Pro Max", "iPhone 16 Pro", "iPhone 16 Plus", "iPhone 16", "iPhone 16e",
              "iPhone 15 Pro Max", "iPhone 15 Pro", "iPhone 15 Plus", "iPhone 15",
              "iPhone 14 Pro Max", "iPhone 14 Pro", "iPhone 14 Plus", "iPhone 14",
              "iPhone 13 Pro Max", "iPhone 13 Pro", "iPhone 13", "iPhone 13 mini",
              "iPhone 12 Pro Max", "iPhone 12 Pro", "iPhone 12", "iPhone 12 mini",
              "iPhone 11 Pro Max", "iPhone 11 Pro", "iPhone 11", "iPhone SE (3rd gen)", "iPhone SE (2nd gen)",
              "iPhone XR", "iPhone XS Max", "iPhone XS", "iPhone X",
              "iPad Pro 13-inch (M4)", "iPad Pro 11-inch (M4)", "iPad Air (M2)", "iPad (10th gen)", "iPad mini (6th gen)",
              "Apple Watch Series 10", "Apple Watch Series 9", "Apple Watch Ultra 2", "Apple Watch SE"],
    "Samsung": ["Galaxy S25 Ultra", "Galaxy S25+", "Galaxy S25", "Galaxy S24 Ultra", "Galaxy S24+", "Galaxy S24", "Galaxy S24 FE",
                "Galaxy S23 Ultra", "Galaxy S23+", "Galaxy S23", "Galaxy S23 FE", "Galaxy S22 Ultra", "Galaxy S22", "Galaxy S21",
                "Galaxy Z Fold6", "Galaxy Z Flip6", "Galaxy Z Fold5", "Galaxy Z Flip5",
                "Galaxy A55", "Galaxy A54", "Galaxy A35", "Galaxy A25", "Galaxy A15", "Galaxy Tab S9", "Galaxy Tab A9"],
    "Google": ["Pixel 9 Pro XL", "Pixel 9 Pro", "Pixel 9", "Pixel 9 Pro Fold", "Pixel 8 Pro", "Pixel 8", "Pixel 8a",
               "Pixel 7 Pro", "Pixel 7", "Pixel 7a", "Pixel 6 Pro", "Pixel 6", "Pixel 6a"],
    "Motorola": ["moto g Power", "moto g Stylus", "moto g play", "razr+", "razr", "edge"],
    "OnePlus": ["OnePlus 13", "OnePlus 12", "OnePlus 11", "OnePlus Nord N30"],
}
_COMPUTER_MODELS = {
    "Apple": ["MacBook Air 13-inch (M3)", "MacBook Air 15-inch (M3)", "MacBook Air 13-inch (M2)", "MacBook Air (M1)",
              "MacBook Pro 14-inch (M4)", "MacBook Pro 16-inch (M4)", "MacBook Pro 14-inch (M3)", "MacBook Pro 13-inch (M2)",
              "iMac 24-inch (M4)", "iMac 24-inch (M3)", "Mac mini (M4)", "Mac Studio"],
    "Dell": ["XPS 13", "XPS 15", "XPS 16", "Inspiron 14", "Inspiron 15", "Inspiron 16", "Latitude 5440", "Latitude 7440",
             "Vostro 15", "Alienware m16", "OptiPlex 7010"],
    "HP": ["Pavilion 15", "Pavilion x360", "Envy x360", "Spectre x360", "EliteBook 840", "ProBook 450", "Victus 15", "OMEN 16", "HP 15 Laptop"],
    "Lenovo": ["ThinkPad X1 Carbon", "ThinkPad T14", "ThinkPad E14", "IdeaPad 3", "IdeaPad 5", "IdeaPad Flex 5", "Yoga 7", "Legion 5", "ThinkCentre M70"],
    "Asus": ["Zenbook 14", "Vivobook 15", "ROG Zephyrus G14", "ROG Strix G16", "TUF Gaming A15"],
    "Acer": ["Aspire 5", "Swift 3", "Nitro 5", "Predator Helios 16", "Chromebook 314"],
    "Microsoft": ["Surface Laptop 7", "Surface Laptop 5", "Surface Pro 11", "Surface Pro 9", "Surface Go 4"],
}
_CONSOLE_MODELS = {
    "Sony": ["PlayStation 5 Pro", "PlayStation 5", "PlayStation 5 Digital", "PlayStation 4 Pro", "PlayStation 4", "DualSense Controller"],
    "Microsoft": ["Xbox Series X", "Xbox Series S", "Xbox One X", "Xbox One S", "Xbox Wireless Controller"],
    "Nintendo": ["Switch OLED", "Switch", "Switch Lite", "Joy-Con"],
    "Valve": ["Steam Deck OLED", "Steam Deck"],
}


def _merge_models(*parts: dict) -> dict:
    out: dict[str, list[str]] = {}
    for p in parts:
        for b, ms in p.items():
            out.setdefault(b, [])
            out[b] += [m for m in ms if m not in out[b]]
    return out


STARTER_MODELS: dict[str, dict[str, list[str]]] = {
    "mobile_phone": _PHONE_MODELS,
    "computer": _merge_models(_COMPUTER_MODELS, {"Samsung": ["Galaxy Book4", "Galaxy Book3"]}),
    "general": _merge_models(_PHONE_MODELS, _COMPUTER_MODELS, _CONSOLE_MODELS),
    "auto": {
        "Toyota": ["Corolla", "Camry", "RAV4", "Tacoma", "Highlander", "Tundra"],
        "Honda": ["Civic", "Accord", "CR-V", "Pilot", "HR-V"],
        "Ford": ["F-150", "Escape", "Explorer", "Mustang", "Bronco", "Ranger"],
        "Chevrolet": ["Silverado 1500", "Equinox", "Malibu", "Tahoe", "Traverse"],
    },
}


# ── Read helpers ─────────────────────────────────────────────────────────────
def normalize_template_key(key: Optional[str]) -> str:
    """Map any stored/legacy value onto a canonical template key."""
    k = (key or "").strip().lower()
    if k in TEMPLATES:
        return k
    return LEGACY_ALIASES.get(k, DEFAULT_TEMPLATE)


def template(key: Optional[str]) -> dict:
    return TEMPLATES[normalize_template_key(key)]


def template_list() -> list[dict]:
    """[{key, label, asset_label, wo_button, group_count}] for the settings picker."""
    out = []
    for k, t in TEMPLATES.items():
        out.append({"key": k, "label": t["label"], "asset_label": t["asset_label"],
                    "wo_button": t["wo_button"], "group_count": len(t["groups"])})
    return out


def wo_button(key: Optional[str]) -> str:
    return template(key)["wo_button"]


def asset_label(key: Optional[str]) -> str:
    return template(key)["asset_label"]


def groups(key: Optional[str]) -> list[dict]:
    # Each group carries its effective identifier rule so the asset/create forms can
    # narrow the serial-type fields further when a group overrides the template
    # (e.g. a Jet Ski under a Marine shop → HIN, a Trailer under Auto → VIN/serial).
    return [{"key": g["key"], "label": g["label"], "identifier": identifier_rule(key, g["key"])}
            for g in template(key)["groups"]]


def _group_def(key: Optional[str], group_key: Optional[str]) -> Optional[dict]:
    if not group_key:
        return None
    for g in template(key)["groups"]:
        if g["key"] == group_key:
            return g
    return None


def identifier_rule(key: Optional[str], group_key: Optional[str] = None) -> dict:
    """Effective {primary, allowed} for a template (+ optional group override)."""
    t = template(key)
    g = _group_def(key, group_key)
    rule = (g or {}).get("identifier") or t["identifier"]
    return {"primary": rule["primary"], "allowed": list(rule["allowed"])}


def field_schema(key: Optional[str], group_key: Optional[str] = None) -> list[dict]:
    """Ordered fields for a template. Identifier fields whose concrete kind is
    group-dependent (serial_vin / vin_serial) are resolved via the group rule."""
    t = template(key)
    rule = identifier_rule(key, group_key)
    out = []
    for fk in t["fields"]:
        base = FIELD_LIBRARY.get(fk)
        if not base:
            continue
        f = {"key": fk, **base}
        if fk in ("serial_vin", "vin_serial"):
            f = {**f, "identifier_kind": rule["primary"],
                 "label": IDENTIFIER_LABELS.get(rule["primary"], f["label"])}
        out.append(f)
    return out


def describe(key: Optional[str]) -> dict:
    """One object that powers a settings preview / the asset forms."""
    ck = normalize_template_key(key)
    t = TEMPLATES[ck]
    return {
        "key": ck, "label": t["label"],
        "asset_label": t["asset_label"], "asset_label_plural": t["asset_label_plural"],
        "wo_button": t["wo_button"], "wo_title": t["wo_title"], "wo_noun": t["wo_noun"],
        "groups": groups(ck),
        "identifier": identifier_rule(ck),
        "fields": field_schema(ck),
    }


# ── Seeding ──────────────────────────────────────────────────────────────────
async def ensure_industry_seed(db: AsyncSession, org_id: str,
                               only_template: Optional[str] = None) -> dict:
    """Materialise default groups, identifier rules and starter brands/models for
    an org. Idempotent — safe to call repeatedly. Never touches another template's
    rows. Returns a small summary of what was created.

    Import models lazily to avoid a circular import at module load."""
    from backend.models import AssetBrand, AssetGroup, AssetIdentifierRule, AssetModel

    keys = [normalize_template_key(only_template)] if only_template else TEMPLATE_KEYS
    created = {"groups": 0, "rules": 0, "brands": 0, "models": 0}

    for tk in keys:
        t = TEMPLATES[tk]

        # existing rows for this org+template (scoped — never cross-template)
        existing_groups = {g.key: g for g in (await db.execute(select(AssetGroup).where(
            AssetGroup.org_id == org_id, AssetGroup.template_key == tk))).scalars().all()}

        # 1) groups
        for i, g in enumerate(t["groups"]):
            if g["key"] not in existing_groups:
                row = AssetGroup(org_id=org_id, template_key=tk, key=g["key"],
                                 label=g["label"], sort_order=i, is_system=True)
                db.add(row)
                await db.flush()
                existing_groups[g["key"]] = row
                created["groups"] += 1

        # 2) identifier rules — template default (asset_group_id null) + per-group
        have_rules = {(r.asset_group_id) for r in (await db.execute(select(AssetIdentifierRule).where(
            AssetIdentifierRule.org_id == org_id, AssetIdentifierRule.template_key == tk))).scalars().all()}
        if None not in have_rules:
            db.add(AssetIdentifierRule(org_id=org_id, template_key=tk, asset_group_id=None,
                                       primary_kind=t["identifier"]["primary"],
                                       allowed_kinds=list(t["identifier"]["allowed"]), is_system=True))
            created["rules"] += 1
        for g in t["groups"]:
            if "identifier" not in g:
                continue
            gid = existing_groups[g["key"]].id
            if gid not in have_rules:
                db.add(AssetIdentifierRule(org_id=org_id, template_key=tk, asset_group_id=gid,
                                           primary_kind=g["identifier"]["primary"],
                                           allowed_kinds=list(g["identifier"]["allowed"]), is_system=True))
                created["rules"] += 1

        # 3) starter brands (template-wide: asset_group_id null)
        existing_brands = {b.name: b for b in (await db.execute(select(AssetBrand).where(
            AssetBrand.org_id == org_id, AssetBrand.template_key == tk,
            AssetBrand.asset_group_id.is_(None)))).scalars().all()}
        for i, name in enumerate(STARTER_BRANDS.get(tk, [])):
            if name not in existing_brands:
                b = AssetBrand(org_id=org_id, template_key=tk, asset_group_id=None,
                               name=name, sort_order=i, is_system=True)
                db.add(b)
                await db.flush()
                existing_brands[name] = b
                created["brands"] += 1

        # 4) starter models under those brands
        for brand_name, models in STARTER_MODELS.get(tk, {}).items():
            brand = existing_brands.get(brand_name)
            if not brand:
                continue
            have_models = {m.name for m in (await db.execute(select(AssetModel).where(
                AssetModel.org_id == org_id, AssetModel.template_key == tk,
                AssetModel.brand_id == brand.id))).scalars().all()}
            for i, mname in enumerate(models):
                if mname not in have_models:
                    db.add(AssetModel(org_id=org_id, template_key=tk, brand_id=brand.id,
                                      name=mname, sort_order=i, is_system=True))
                    created["models"] += 1

    await db.commit()
    return created
