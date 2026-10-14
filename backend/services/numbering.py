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
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import NumberSequence

# Built-in defaults mirror the pre-Phase-1 hardcoded formats exactly.
DEFAULT_TEMPLATES = {
    "order": "WO-{C:5}",
    "invoice": "INV-{C:5}",
    "inquiry": "INQ-{C:5}",
    "sale": "S-{C:5}",
    "po": "PO-{C:5}",
    "transfer": "T-{C:5}",
}

_TOKEN = re.compile(r"\{(D|M|Y|C(?::(\d+))?)\}")


def render(template: str, counter: int, *, now: datetime | None = None) -> str:
    now = now or datetime.utcnow()

    def sub(m: re.Match) -> str:
        tok = m.group(1)
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
            "po": "PurchaseOrder", "transfer": "StockTransfer", "inquiry": "Order"}.get(doc_type)
    model = getattr(M, name, None) if name else None
    if model is None or not hasattr(model, "org_id"):
        return 0
    try:
        return (await db.execute(_sel(func.count()).select_from(model).where(model.org_id == org_id))).scalar() or 0
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
        template = DEFAULT_TEMPLATES.get(doc_type, doc_type.upper() + "-{C:5}")
        seq = NumberSequence(org_id=org_id, doc_type=doc_type, scope_key="org",
                             template=template, next_value=await _existing_count(db, org_id, doc_type) + 1)
        db.add(seq)
        await db.flush()
    counter = seq.next_value
    seq.next_value = counter + 1
    seq.updated_at = datetime.utcnow()
    await db.flush()
    return render(seq.template, counter)


async def peek_example(template: str, begins_from: int | None = None) -> str:
    """Render a sample number for the Settings UI without touching any counter."""
    return render(template or "", begins_from or 1)
