"""D2 — link every call to a contact, and create the contact when the number is new.

The automation lives in AutomationSetting key ``contact_from_call``:
    enabled            bool
    config.direction   "incoming" (only inbound calls create contacts) | "all"
    config.tag_ids     tags applied to each contact it creates (must belong to the shop)
    config.assign_manager  set the new contact's manager: the employee who placed the call,
                       else the employee who owns the dialled extension

Numbers are compared normalised (digits only; the last 10 digits when both numbers have at
least 10, so "+1 555 010 0123", "(555) 010-0123" and "5550100123" are the same contact), and
an existing match always wins, so the automation never creates a duplicate.
"""
import re
from typing import Optional

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import AutomationSetting, Call, Client, Employee, Organisation, Tag, Taggable
from backend.services import audit

AUTOMATION_KEY = "contact_from_call"
DEFAULT_CONFIG = {"direction": "incoming", "tag_ids": [], "assign_manager": True}


def digits(number: Optional[str]) -> str:
    d = re.sub(r"\D", "", number or "")
    return d[2:] if (number or "").strip().startswith("00") else d


def _intl(number: Optional[str]) -> bool:
    return (number or "").strip().startswith(("+", "00"))


def same_number(a: Optional[str], b: Optional[str]) -> bool:
    da, db_ = digits(a), digits(b)
    if not da or not db_:
        return False
    if da == db_:
        return True
    if _intl(a) and _intl(b):      # both carry a country code, so they must agree in full
        return False
    if _intl(a) != _intl(b):       # "+65 9123 4567" vs "9123 4567", "+44 7700…" vs "07700…"
        full, nat = (da, db_) if _intl(a) else (db_, da)
        nat = nat[1:] if nat.startswith("0") else nat      # national trunk prefix
        return len(nat) >= 7 and full.endswith(nat) and len(full) - len(nat) <= 3
    return len(da) >= 10 and len(db_) >= 10 and da[-10:] == db_[-10:]


def format_phone(number: str) -> str:
    """Readable form used as the new contact's name: NANP numbers as +1 (555) 010-0123,
    anything else as +<digits> (or as typed when it has no digits at all)."""
    d = digits(number)
    intl = _intl(number)
    if len(d) == 11 and d.startswith("1"):
        return f"+1 ({d[1:4]}) {d[4:7]}-{d[7:]}"
    if len(d) == 10 and not intl:
        return f"({d[:3]}) {d[3:6]}-{d[6:]}"
    if d:
        return f"+{d}" if intl else d
    return (number or "").strip() or "Unknown caller"


# ── Outbound destinations (integration review S3: toll fraud) ────────────────
# Country → international calling code. Click-to-call only reaches the shop's own country by
# default; a shop adds others in Settings → Calls & telephony.
CALLING_CODES = {
    "US": "1", "CA": "1", "PR": "1", "GB": "44", "IE": "353", "AU": "61", "NZ": "64", "IN": "91",
    "AE": "971", "SA": "966", "QA": "974", "KW": "965", "BH": "973", "OM": "968", "EG": "20",
    "ZA": "27", "NG": "234", "KE": "254", "DE": "49", "FR": "33", "ES": "34", "IT": "39",
    "PT": "351", "NL": "31", "BE": "32", "LU": "352", "CH": "41", "AT": "43", "DK": "45",
    "SE": "46", "NO": "47", "FI": "358", "IS": "354", "PL": "48", "CZ": "420", "SK": "421",
    "HU": "36", "RO": "40", "BG": "359", "GR": "30", "HR": "385", "SI": "386", "RS": "381",
    "UA": "380", "TR": "90", "IL": "972", "JO": "962", "LB": "961", "PK": "92", "BD": "880",
    "LK": "94", "NP": "977", "SG": "65", "MY": "60", "TH": "66", "VN": "84", "PH": "63",
    "ID": "62", "HK": "852", "TW": "886", "JP": "81", "KR": "82", "CN": "86", "MX": "52",
    "BR": "55", "AR": "54", "CL": "56", "CO": "57", "PE": "51",
}
_KNOWN_CODES = sorted(set(CALLING_CODES.values()), key=len, reverse=True)


def code_for_number(number: Optional[str]) -> Optional[str]:
    """The calling code an international-format number starts with (longest known match)."""
    if not _intl(number):
        return None
    d = digits(number)
    return next((c for c in _KNOWN_CODES if d.startswith(c)), None)


def clean_prefixes(values) -> list[str]:
    """'+44', '44 ', '0044' → '44'; drops anything that isn't 1–4 digits."""
    out = []
    for v in values or []:
        d = re.sub(r"\D", "", str(v))
        d = d[2:] if str(v).strip().startswith("00") else d
        if 1 <= len(d) <= 4 and d not in out:
            out.append(d)
    return out


def default_prefixes(country: Optional[str], caller_id: Optional[str]) -> list[str]:
    code = CALLING_CODES.get((country or "").upper()) or code_for_number(caller_id)
    return [code] if code else []


def destination_allowed(number: str, prefixes: list[str]) -> bool:
    """National-format numbers stay inside the shop's own country; an international number
    must start with one of the allowed calling codes."""
    if not _intl(number):
        return True
    d = digits(number)
    return any(d.startswith(p) for p in prefixes)


async def find_client(db: AsyncSession, org_id: str, number: Optional[str], *,
                      newest: bool = False) -> Optional[Client]:
    d = digits(number)
    if len(d) < 4:
        return None
    # Narrow in SQL on the trailing digits of the phone with its usual formatting stripped,
    # then compare the normalised numbers in Python. On legacy duplicates the oldest contact
    # wins, or the newest with newest=True (the chat inbox's Orderry rule).
    stored = Client.phone
    for ch in (" ", "-", "(", ")", ".", "/", "+"):
        stored = func.replace(stored, ch, "")
    rows = (await db.execute(select(Client).where(
        Client.org_id == org_id, Client.phone.isnot(None), stored.like(f"%{d[-7:]}%"))
        .order_by(*((Client.created_at.desc(), Client.id.desc()) if newest
                    else (Client.created_at, Client.id))))).scalars().all()
    return next((c for c in rows if same_number(c.phone, number)), None)


async def settings_for(db: AsyncSession, org_id: str) -> dict:
    s = (await db.execute(select(AutomationSetting).where(
        AutomationSetting.org_id == org_id,
        AutomationSetting.key == AUTOMATION_KEY))).scalar_one_or_none()
    cfg = dict(DEFAULT_CONFIG)
    if s and isinstance(s.config, dict):
        cfg.update({k: s.config[k] for k in DEFAULT_CONFIG if k in s.config})
    if cfg.get("direction") not in ("incoming", "all"):
        cfg["direction"] = "incoming"
    cfg["tag_ids"] = [t for t in (cfg.get("tag_ids") or []) if isinstance(t, str)]
    cfg["assign_manager"] = bool(cfg.get("assign_manager"))
    return {"enabled": bool(s and s.enabled), "config": cfg}


async def extension_owner(db: AsyncSession, org_id: str, *, extension: Optional[str],
                          to_number: Optional[str]) -> Optional[Employee]:
    """The active employee whose extension was dialled: the provider's explicit extension,
    else a dialled number that IS an extension (exact match, never a suffix of a full
    number, so the shop's main line doesn't pick whoever's extension it happens to end with)."""
    staff = (await db.execute(select(Employee).where(
        Employee.org_id == org_id, Employee.active == True,  # noqa: E712
        Employee.extension.isnot(None), Employee.extension != "")
        .order_by(Employee.id))).scalars().all()
    ext = (extension or "").strip()
    if ext:
        return next((e for e in staff if e.extension.strip() == ext), None)
    to = digits(to_number)
    if to:
        return next((e for e in staff if digits(e.extension) == to), None)
    return None


async def _lock_number(db: AsyncSession, org_id: str, number: Optional[str]) -> None:
    """Serialise find-or-create for one number until the caller's transaction ends, so two
    simultaneous calls from a new number can't both create a contact. Postgres advisory
    transaction lock; SQLite (dev/test) already serialises writers."""
    d = digits(number)
    if not d or db.bind.dialect.name != "postgresql":
        return
    import hashlib
    key = int.from_bytes(hashlib.sha256(f"call-contact:{org_id}:{d[-10:]}".encode()).digest()[:8],
                         "big", signed=True)
    await db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": key})


async def link_call(db: AsyncSession, call: Call, *, extension: Optional[str] = None,
                    may_create: bool = True) -> dict:
    """Set call.client_id from the caller/callee number, creating the contact when the
    automation allows it (and may_create: the caller's role may create contacts).
    Flushes but does not commit. Returns {"client_id", "created"}."""
    number = call.from_number if call.direction == "inbound" else call.to_number
    await _lock_number(db, call.org_id, number)
    existing = await find_client(db, call.org_id, number)
    if existing:
        call.client_id = existing.id
        return {"client_id": existing.id, "created": False}
    if not digits(number):
        return {"client_id": None, "created": False}
    auto = await settings_for(db, call.org_id)
    cfg = auto["config"]
    if not may_create or not auto["enabled"] or (call.direction != "inbound" and cfg["direction"] != "all"):
        return {"client_id": None, "created": False}

    manager_id = None
    if cfg["assign_manager"]:
        if call.employee_id:
            manager_id = call.employee_id
        else:
            owner = await extension_owner(db, call.org_id, extension=extension,
                                          to_number=call.to_number)
            manager_id = owner.id if owner else None

    org = (await db.execute(select(Organisation).where(Organisation.id == call.org_id))).scalar_one()
    client = Client(org_id=call.org_id, name=format_phone(number), phone=(number or "").strip(),
                    source="call", manager_id=manager_id,
                    consent_phone=org.contact_consent_phone_default,
                    consent_email=org.contact_consent_email_default)
    db.add(client)
    await db.flush()
    await audit.record(db, org_id=call.org_id, actor_id=call.employee_id, action="client.create",
                       entity_type="client", entity_id=client.id,
                       diff={"name": client.name, "source": "call", "call_id": call.id})
    if cfg["tag_ids"]:
        tags = (await db.execute(select(Tag.id).where(
            Tag.org_id == call.org_id, Tag.id.in_(cfg["tag_ids"])))).scalars().all()
        for tid in tags:
            db.add(Taggable(org_id=call.org_id, tag_id=tid, entity_type="contact",
                            entity_id=client.id))
    call.client_id = client.id
    await db.flush()
    return {"client_id": client.id, "created": True}
