"""Document numbering engine (Phase 1).

A template with tokens resolves to a document number against a per-scope atomic
counter. Tokens:
  {D} = 2-digit day, {M} = 2-digit month, {Y} = 4-digit year,
  {C:n} = zero-padded incrementing counter of n digits (e.g. {C:4} -> 0007).

Resolution scope order (most specific wins): entity -> location -> org.
Absent any configured sequence, a built-in default template is used so existing
installs keep their current format (e.g. INV-00001).
"""
import re
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import NumberSequence
from backend.services.dates import now_utc

# Built-in defaults mirror the pre-Phase-1 hardcoded formats exactly.
DEFAULT_TEMPLATES = {
    "order": "WO-{C:5}",
    "invoice": "INV-{C:5}",
    "inquiry": "INQ-{C:5}",
    "sale": "S-{C:5}",
    "po": "PO-{C:5}",
    "transfer": "T-{C:5}",
    "credit_note": "CN-{C:5}",
}

_TOKEN = re.compile(r"\{(D|M|Y|C(?::(\d+))?|CAT|T)\}")


def render(template: str, counter: int, *, now: datetime | None = None, tokens: dict | None = None) -> str:
    """`tokens` fills the code-template words: {CAT} (category) and {T} (asset type)."""
    now = now or now_utc()

    def sub(m: re.Match) -> str:
        tok = m.group(1)
        if tok in ("CAT", "T"):
            return (tokens or {}).get(tok, "")
        if tok == "D":
            return f"{now.day:02d}"
        if tok == "M":
            return f"{now.month:02d}"
        if tok == "Y":
            return f"{now.year:04d}"
        if tok.startswith("C"):
            width = int(m.group(2)) if m.group(2) else 0
            return f"{counter:0{width}d}" if width else str(counter)
        return m.group(0)

    return _TOKEN.sub(sub, template)


def _scope_candidates(location_id: str | None, entity_id: str | None) -> list[str]:
    keys = []
    if entity_id:
        keys.append(f"entity:{entity_id}")
    if location_id:
        keys.append(f"loc:{location_id}")
    keys.append("org")
    return keys


async def _find_sequence(db, org_id, doc_type, location_id, entity_id):
    for key in _scope_candidates(location_id, entity_id):
        seq = (await db.execute(select(NumberSequence).where(
            NumberSequence.org_id == org_id,
            NumberSequence.doc_type == doc_type,
            NumberSequence.scope_key == key))).scalar_one_or_none()
        if seq:
            return seq
    return None


_DOC_MODEL = {"order": ("Order", "org_id"), "invoice": ("Invoice", "org_id"),
              "inquiry": ("Order", "org_id"), "sale": ("Sale", "org_id"),
              "po": ("PurchaseOrder", None), "transfer": ("StockTransfer", "org_id")}


async def _existing_count(db, org_id, doc_type) -> int:
    """Best-effort count of existing docs of this type for seeding a new sequence."""
    from sqlalchemy import func
    from sqlalchemy import select as _sel

    import backend.models as M
    name = {"order": "Order", "invoice": "Invoice", "sale": "Sale",
            "po": "PurchaseOrder", "transfer": "StockTransfer", "inquiry": "Order",
            "credit_note": "CreditNote"}.get(doc_type)
    model = getattr(M, name, None) if name else None
    if model is None or not hasattr(model, "org_id"):
        return 0
    q = _sel(func.count()).select_from(model).where(model.org_id == org_id)
    if doc_type == "credit_note":
        q = q.where(model.number.isnot(None))     # drafts carry no number until issued
    try:
        return (await db.execute(q)).scalar() or 0
    except Exception:
        return 0


async def next_number(
    db: AsyncSession,
    org_id: str,
    doc_type: str,
    *,
    location_id: str | None = None,
    entity_id: str | None = None,
) -> str:
    """Resolve and atomically increment the counter for this doc/scope, returning
    the rendered document number. Caller commits within its own transaction."""
    seq = await _find_sequence(db, org_id, doc_type, location_id, entity_id)
    if seq is None:
        # Lazily create an org-scope sequence from the built-in default. Seed the
        # counter *past any existing documents* of this type so a fresh sequence
        # never collides with legacy count-based numbers already in the table.
        # Insert-or-ignore: a shop's first two documents created at once both found no sequence,
        # and the second insert hit uq_numseq_scope (a 500). The loser just uses the winner's row.
        from backend.db.database import insert_ignore
        template = DEFAULT_TEMPLATES.get(doc_type, doc_type.upper() + "-{C:5}")
        await insert_ignore(db, NumberSequence, org_id=org_id, doc_type=doc_type, scope_key="org",
                            template=template, next_value=await _existing_count(db, org_id, doc_type) + 1)
        seq = await _find_sequence(db, org_id, doc_type, location_id, entity_id)
    # Claim counters with an atomic UPDATE (row-locked on Postgres): reading next_value and writing
    # +1 in Python let two concurrent documents take the same number.
    from sqlalchemy import update

    from backend.services.dates import shop_tzname, zone
    # {D}/{M}/{Y} are the shop's date: a document made in the evening west of UTC is today's.
    local_now = now_utc().replace(tzinfo=timezone.utc).astimezone(zone(await shop_tzname(db, org_id)))
    for _ in range(100):
        await db.execute(update(NumberSequence).where(NumberSequence.id == seq.id)
                         .values(next_value=NumberSequence.next_value + 1, updated_at=now_utc())
                         .execution_options(synchronize_session=False))
        await db.refresh(seq, attribute_names=["next_value"])
        number = render(seq.template, seq.next_value - 1, now=local_now)
        # Skip numbers already taken (legacy count-based invoices, a sequence reset in Settings).
        if not await _number_taken(db, org_id, doc_type, number):
            return number
    raise RuntimeError(f"Could not find a free {doc_type} number — check the numbering settings")


async def _number_taken(db: AsyncSession, org_id: str, doc_type: str, number: str) -> bool:
    from backend.models import CreditNote, Invoice, PurchaseOrder
    col = {"invoice": (Invoice, Invoice.invoice_number), "po": (PurchaseOrder, PurchaseOrder.po_number),
           "credit_note": (CreditNote, CreditNote.number)}.get(doc_type)
    if col is None:
        return False
    model, number_col = col
    return (await db.execute(select(model.id).where(
        model.org_id == org_id, number_col == number).limit(1))).scalar_one_or_none() is not None


async def peek_example(template: str, begins_from: int | None = None) -> str:
    """Render a sample number for the Settings UI without touching any counter."""
    return render(template or "", begins_from or 1)


# ── Product / bin / asset codes (ii144a) ─────────────────────────────────────────────────────────
# Same counter table and skip-if-taken rule as documents; the template comes from the shop's
# Settings › Inventory (an empty setting uses the default below). Existing codes are never rewritten:
# these only fill empty fields.
CODE_DEFAULTS = {"sku": "{C:6}", "product_serial": "SN{C:6}", "bin": "BIN-{C:4}", "asset_serial": "{T}-{C:6}"}
CODE_KINDS = (*CODE_DEFAULTS, "barcode_ean13", "barcode_upca", "barcode_ean8")
_TEMPLATE_TEXT = re.compile(r"^[A-Za-z0-9 _./#-]*$")
MAX_TEMPLATE = 40
MAX_COUNTER_WIDTH = 20      # Orderry examples go to {C:19}


def template_problem(template: str) -> str | None:
    """A plain reason a code template can't be used, or None. Only known tokens and plain text."""
    t = template or ""
    if len(t) > MAX_TEMPLATE:
        return f"Keep the pattern to {MAX_TEMPLATE} characters or fewer."
    counters = list(re.finditer(r"\{C(?::(\d+))?\}", t))
    if len(counters) != 1:
        return "The pattern needs exactly one counter, like {C:5}."
    if counters[0].group(1) and int(counters[0].group(1)) > MAX_COUNTER_WIDTH:
        return f"A counter can have at most {MAX_COUNTER_WIDTH} digits."
    if not _TEMPLATE_TEXT.match(_TOKEN.sub("", t)):
        return "Use letters, numbers, spaces and - _ . / # around {C:n}, {CAT}, {T}, {Y}, {M}, {D}."
    return None


def code_word(value: str | None, fallback: str) -> str:
    """{CAT} / {T}: the first three letters or digits of a name, upper-cased."""
    w = re.sub(r"[^A-Za-z0-9]", "", value or "").upper()[:3]
    return w or fallback


async def claim(db: AsyncSession, org_id: str, kind: str, n: int = 1) -> int:
    """Atomically take `n` consecutive counter values for this shop's `kind`; returns the first."""
    from sqlalchemy import update

    from backend.db.database import insert_ignore
    await insert_ignore(db, NumberSequence, org_id=org_id, doc_type=kind, scope_key="org",
                        template=CODE_DEFAULTS.get(kind, kind), next_value=1)
    seq = (await db.execute(select(NumberSequence).where(
        NumberSequence.org_id == org_id, NumberSequence.doc_type == kind,
        NumberSequence.scope_key == "org"))).scalar_one()
    await db.execute(update(NumberSequence).where(NumberSequence.id == seq.id)
                     .values(next_value=NumberSequence.next_value + n, updated_at=datetime.utcnow())
                     .execution_options(synchronize_session=False))
    await db.refresh(seq, attribute_names=["next_value"])
    return seq.next_value - n


async def next_codes(db: AsyncSession, org_id: str, kind: str, n: int, *, render_fn, taken) -> list[str]:
    """`n` fresh codes: counters claimed in blocks, each rendered by `render_fn(counter)`, and any
    that `taken(set_of_codes) -> set` reports as already in use skipped (a code typed by hand
    earlier, a counter reset). Raises when the pattern can't produce enough free codes."""
    out: list[str] = []
    for _ in range(50):
        want = n - len(out)
        if want <= 0:
            break
        start = await claim(db, org_id, kind, want)
        batch = [render_fn(c) for c in range(start, start + want)]
        used = await taken(set(batch)) if batch else set()
        out.extend(c for c in batch if c not in used and c not in out)
    if len(out) < n:
        from fastapi import HTTPException
        raise HTTPException(409, "Couldn't find enough free codes — check the pattern in Settings › Inventory")
    return out


async def next_code(db: AsyncSession, org_id: str, kind: str, template: str | None, *,
                    tokens: dict | None = None, taken) -> str:
    tpl = (template or "").strip() or CODE_DEFAULTS[kind]
    return (await next_codes(db, org_id, kind, 1, render_fn=lambda c: render(tpl, c, tokens=tokens),
                             taken=taken))[0]


# GS1 "restricted circulation" (in-store) prefixes: codes no manufacturer is ever issued, so a
# generated barcode can't collide with a real product's and scans at any till.
IN_STORE_PREFIXES = tuple(str(p) for p in range(20, 30))


def in_store_ean13(prefix: str, counter: int) -> str:
    from backend.services.codes import gtin_check_digit
    if prefix not in IN_STORE_PREFIXES:
        raise ValueError("Generated barcodes use the in-store range 20–29")
    if not 0 <= counter < 10 ** 10:
        raise ValueError("Barcode counter out of range")
    body = f"{prefix}{counter:010d}"
    return body + gtin_check_digit(body)


def in_store_gtin(kind: str, prefix: str, counter: int) -> str:
    """EAN-13 in 20–29; UPC-A number system 2 (in-store); EAN-8 prefix 2 (restricted circulation)."""
    from backend.services.codes import gtin_check_digit
    if kind == "ean13":
        return in_store_ean13(prefix, counter)
    body = f"2{counter:010d}" if kind == "upca" else f"2{counter:06d}"
    if len(body) != (11 if kind == "upca" else 7):
        raise ValueError("Barcode counter out of range")
    return body + gtin_check_digit(body)
