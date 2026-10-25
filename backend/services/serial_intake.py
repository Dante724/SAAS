"""Serial / IMEI intake: one set of rules for every screen that brings serial-tracked stock in.

The live check behind the scan box, the posting commit and PO receiving all call into here,
so what the screen shows and what the server accepts can never disagree.

Rules (council note 2026-10-24, workstream A):
- **Blocked**: the same serial twice in one intake; a serial already in stock in this shop
  (in stock or reserved on a job); serial count different from the quantity; a fractional
  quantity of a serial-tracked product; a serial-tracked product with no serials.
- **Warning only**: an IMEI whose length or check digit is wrong (the box is in front of
  you, a sticker can be misprinted); a serial this shop has had before that was sold,
  written off or turned into an asset (trade-ins and returns happen).

Every lookup is scoped to the caller's shop: another shop's serials read as new.
"""
import re
from typing import Annotated, Iterable, Optional

from fastapi import HTTPException
from pydantic import StringConstraints
from sqlalchemy import func, select, text

from backend.services import identifiers as _ids

MAX_SERIALS = 500
MAX_LEN = 120
IN_STOCK = ("in_stock", "allocated_to_order")
_SPLIT = re.compile(r"[\r\n,;\t]+")

# One entered serial, as request bodies take it (lists of these are capped at MAX_SERIALS).
SerialStr = Annotated[str, StringConstraints(max_length=MAX_LEN)]

_TYPE_WORDS = {
    "serialized": ("serial", "serialized", "serialised", "imei", "sn", "s/n", "serial no", "serial number",
                   "serial-tracked", "serial tracked", "tracked by serial"),
    "expiry": ("expiry", "expiry-tracked", "expiry tracked", "expires", "expiration", "expiring",
               "perishable", "best before", "use by", "batch", "lot"),
}


def split_serials(raw: Optional[str]) -> list[str]:
    """A pasted block → one serial per entry. Newlines, commas, semicolons and tabs separate
    entries; spaces inside an entry are kept for `normalize` (IMEIs are often printed grouped)."""
    if not raw:
        return []
    return [p.strip() for p in _SPLIT.split(raw) if p.strip()]


def normalize(value: Optional[str]) -> tuple[Optional[str], str]:
    """(stored form, kind). A value that is only digits (after spaces / dashes) of IMEI length
    is an IMEI and keeps digits only; anything else is a serial, upper-cased without spaces."""
    v = (value or "").strip()
    if not v:
        return None, "serial"
    if _ids.guess_kind(v) == "imei":
        return _ids.normalize("imei", v), "imei"
    return _ids.normalize("serial", v), "serial"


def parse_product_type(raw: Optional[str]) -> str:
    """A supplier sheet's "Type" cell → standard | serialized | expiry."""
    t = re.sub(r"[^a-z/ -]", "", (raw or "").strip().lower()).strip()
    if not t:
        return "standard"
    for kind, words in _TYPE_WORDS.items():
        if t in words or any(t.startswith(w) for w in words if len(w) > 3):
            return kind
    return "standard"


async def check(db, org_id: str, values: Iterable[str]) -> list[dict]:
    """Classify each entered value. Never writes. One query for the whole list."""
    from backend.models import Part, SerializedItem

    entries = []
    for raw in values:
        norm, kind = normalize(raw)
        entries.append((raw, norm, kind))
    wanted = {n for _, n, _ in entries if n}
    known: dict[str, list[tuple[str, str]]] = {}
    if wanted:
        # Serials saved before intake normalised them may still carry spaces: compare without.
        stored = func.upper(func.replace(SerializedItem.serial_number, " ", ""))
        rows = (await db.execute(
            select(stored, SerializedItem.status, Part.name)
            .join(Part, Part.id == SerializedItem.part_id)
            .where(SerializedItem.org_id == org_id, stored.in_(wanted)))).all()
        for sn, status, pname in rows:
            known.setdefault(sn, []).append((status, pname))

    seen: set[str] = set()
    out = []
    for raw, norm, kind in entries:
        row = {"input": raw, "serial": norm, "kind": kind, "status": "ok", "reason": None,
               "message": None, "part_name": None}
        if not norm:
            row.update(status="block", reason="empty", message="Empty entry.")
        elif norm in seen:
            row.update(status="block", reason="duplicate", message="Entered twice in this delivery.")
        else:
            hist = known.get(norm, [])
            live = next((h for h in hist if h[0] in IN_STOCK), None)
            if live:
                row.update(status="block", reason="in_stock", part_name=live[1],
                           message=f"Already in stock as {live[1]}.")
            elif hist:
                row.update(status="warn", reason="sold_before", part_name=hist[0][1],
                           message=f"Had this before ({_STATUS_WORDS.get(hist[0][0], hist[0][0])}) — "
                                   "check it's a trade-in or return.")
            elif kind == "imei" and _ids.problem("imei", norm):
                row.update(status="warn", reason="imei_check", message=_ids.problem("imei", norm))
        if norm:
            seen.add(norm)
        out.append(row)
    return out


_STATUS_WORDS = {"sold": "sold", "written_off": "written off", "converted_to_asset": "turned into an asset"}


async def _lock_shop_intake(db, org_id: str) -> None:
    """Serialise serial-tracked intake per shop until the transaction ends, so two deliveries
    scanned at the same moment can't both pass the "already in stock" check with one IMEI.
    Postgres only; SQLite already allows a single writer at a time."""
    if db.get_bind().dialect.name == "postgresql":
        await db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"serial-intake:{org_id}"})


def summary(rows: list[dict]) -> dict:
    return {s: sum(1 for r in rows if r["status"] == s) for s in ("ok", "warn", "block")}


async def validate_intake(db, org_id: str, part, qty: float, serials: Optional[list[str]],
                          seen: Optional[set] = None) -> list[str]:
    """Check the serials for one intake line and return them in stored form.

    `seen` carries serials across the lines of one document, so the same unit can't be
    entered on two lines. Raises 422 / 409 with a plain message when the line can't post."""
    serials = [s for s in (serials or []) if s and str(s).strip()]
    name = part.name or "this product"
    if not part.serialized:
        if serials:
            raise HTTPException(422, f"“{name}” isn't tracked by serial number, so it can't take serials. "
                                     "Turn on serial tracking for the product first.")
        return []
    if abs(qty - round(qty)) > 1e-9:
        raise HTTPException(422, f"“{name}” is tracked by serial number, so the quantity must be a whole number.")
    want = int(round(qty))
    if len(serials) != want:
        raise HTTPException(422, f"Scan or type a serial for each unit of “{name}”: "
                                 f"{len(serials)} of {want} entered.")
    for s in serials:
        if len(str(s)) > MAX_LEN:
            raise HTTPException(422, f"A serial for “{name}” is longer than {MAX_LEN} characters.")
    await _lock_shop_intake(db, org_id)
    rows = await check(db, org_id, serials)
    out = []
    for r in rows:
        if r["reason"] == "duplicate" or (seen is not None and r["serial"] in seen):
            raise HTTPException(409, f"{r['serial']} is entered twice. Each unit needs its own serial.")
        if r["reason"] == "in_stock":
            raise HTTPException(409, f"{r['serial']} is already in stock ({r['part_name']}). "
                                     "Remove it, or write the old unit off first.")
        if r["status"] == "block":
            raise HTTPException(422, f"A serial for “{name}” is empty.")
        out.append(r["serial"])
    if seen is not None:
        seen.update(out)
    return out
