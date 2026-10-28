"""Kits print as one line, extras still shown.

A kit (bundle) explodes into real component lines on the job — money, stock, cost, commission,
warranty and per-line tax all stay on those lines. This module only changes what the CUSTOMER sees
and keeps a fixed kit price exact:

- `customer_lines()` is the one way a customer-facing surface (invoice, job printout, track page,
  portal, e-invoice) gets a job's lines. A kit set to print as one line is folded into a single
  row (name, qty, the sum of its billed lines); "one line and what's included" adds the component
  NAMES only — never their price, cost, qty or SKU. Lines added outside the kit ("extras") always
  print as themselves.
- `spread()` divides a fixed kit total across its component lines in proportion to their standard
  price, with the rounding remainder on one line, so the lines add up to the kit price exactly.

Nothing here touches the database; callers pass lines in and write results back.
"""
from dataclasses import dataclass, field
from math import gcd
from typing import Any, Optional

from backend.services.money import q2

ITEMIZED, ONE_LINE, ONE_LINE_INCLUDES = "itemized", "one_line", "one_line_includes"
PRINT_MODES = (ITEMIZED, ONE_LINE, ONE_LINE_INCLUDES)
# Plain words for the settings screen and audit log.
MODE_LABELS = {ITEMIZED: "Item by item", ONE_LINE: "One line",
               ONE_LINE_INCLUDES: "One line and what's included"}


def norm_mode(mode: Optional[str]) -> str:
    return mode if mode in PRINT_MODES else ITEMIZED


@dataclass
class KitLine:
    """A folded kit as the customer sees it. Duck-types the OrderLine attributes the document
    renderers read, with nothing that could leak a component (no ref_id, cost or SKU).
    `description` is the printed text (the kit name, plus what's included when set to show it)."""
    description: str
    qty: float
    unit_price: float
    total: float
    order_item_id: Optional[str] = None
    order_id: Optional[str] = None
    bundle_group_id: Optional[str] = None
    includes: list[str] = field(default_factory=list)
    tax_rate: float = 0.0
    tax_mode: str = "imposed"
    # Components, for per-rate tax maths only. Never serialised (see row()).
    members: list[Any] = field(default_factory=list, repr=False)
    id: None = None
    ref_id: None = None
    kind: str = "kit"
    line_type: str = "kit"
    cost_price: float = 0.0
    discount: float = 0.0
    discount_type: str = "pct"
    technician_id: None = None
    co_technicians: list = field(default_factory=list)
    warranty_days: int = 0
    pending_stock: float = 0.0
    is_kit: bool = True
    name: str = ""
    _mode: str = field(default=ITEMIZED, repr=False)

    @property
    def is_parts_only(self) -> bool:
        return all((getattr(m, "line_type", "") or "") == "part" for m in self.members)


def component_name(line) -> str:
    """A component's own name: its description without the " (Kit name)" suffix it was given."""
    desc = (getattr(line, "description", "") or "").strip()
    suffix = f" ({getattr(line, 'bundle_name', '') or ''})"
    return desc[:-len(suffix)] if suffix != " ()" and desc.endswith(suffix) else desc


def _line_total(line) -> float:
    return float(line.total if line.total is not None else (line.qty or 0) * (line.unit_price or 0))


def customer_lines(lines, *, itemised: bool = False) -> list:
    """Fold kit groups for a customer document. `lines` are in print order; a folded kit takes
    the place of its first line. A kit is folded per job item (several items per job), and only
    over the lines passed in — a partial invoice folds just the lines it bills. itemised=True is
    the "Print itemised copy" path: every line as itself. Untagged lines (extras, and kits added
    before this feature) are returned unchanged, as the same objects."""
    lines = list(lines)
    if itemised:
        return lines
    groups: dict = {}
    out: list = []
    for line in lines:
        gid = getattr(line, "bundle_group_id", None)
        mode = norm_mode(getattr(line, "bundle_print_mode", None))
        if not gid or mode == ITEMIZED:
            out.append(line)
            continue
        key = (getattr(line, "order_id", None), gid, getattr(line, "order_item_id", None))
        kl = groups.get(key)
        if kl is None:
            kname = getattr(line, "bundle_name", None) or "Kit"
            kl = KitLine(description=kname, name=kname,
                         qty=float(getattr(line, "bundle_qty", None) or 1), unit_price=0.0, total=0.0,
                         order_item_id=getattr(line, "order_item_id", None),
                         order_id=getattr(line, "order_id", None), bundle_group_id=gid, _mode=mode)
            groups[key] = kl
            out.append(kl)
        kl.members.append(line)
    for kl in groups.values():
        kl.total = q2(sum(_line_total(m) for m in kl.members))
        kl.unit_price = q2(kl.total / kl.qty) if kl.qty else kl.total
        rates = {(float(m.tax_rate or 0), m.tax_mode or "imposed") for m in kl.members}
        if len(rates) == 1:
            kl.tax_rate, kl.tax_mode = next(iter(rates))
        if kl._mode == ONE_LINE_INCLUDES:
            seen: list[str] = []
            for m in kl.members:
                n = component_name(m)
                if n and n not in seen:
                    seen.append(n)
            kl.includes = seen
            if seen:
                kl.description = f"{kl.name} (includes {', '.join(seen)})"
    return out


def row(line) -> dict:
    """A customer line as a plain display row ({description, qty, unit_price, total})."""
    if getattr(line, "is_kit", False):
        r = {"description": line.description, "qty": line.qty, "unit_price": line.unit_price,
             "total": line.total, "kit": True}
        if line.includes:
            r["includes"] = list(line.includes)
        return r
    return {"description": line.description, "qty": line.qty, "unit_price": line.unit_price,
            "total": _line_total(line)}


def tax_by_rate(lines, numbers) -> list[dict]:
    """Tax summary split by rate, from the REAL lines (folding never merges rates).
    `numbers(line)` returns {"base", "tax"} for one line."""
    agg: dict = {}
    for line in lines:
        rate = float(getattr(line, "tax_rate", 0) or 0)
        if not rate:
            continue
        n = numbers(line)
        a = agg.setdefault(rate, {"rate": rate, "base": 0.0, "tax": 0.0})
        a["base"] += n["base"]
        a["tax"] += n["tax"]
    return [{"rate": r["rate"], "base": q2(r["base"]), "tax": q2(r["tax"])}
            for r in sorted(agg.values(), key=lambda x: x["rate"])]


# ── fixed kit price: spread a total across the kit's lines, exactly ─────────────────────────────

def _int_qty(q: float) -> Optional[int]:
    return int(round(q)) if q and abs(q - round(q)) < 1e-9 else None


def spread(target: float, members: list) -> list[tuple[Any, float, float]]:
    """Divide `target` (the kit's total, before discounts and tax) across `members` (lines with
    qty and bundle_std_price/unit_price) in proportion to standard price × qty.

    Returns [(line, unit_price, adjust)] where adjust is a sub-cent-per-unit correction to add to
    that line's total; it is 0 unless no line can take the remainder in whole cents (every line
    has qty > 1 and the kit total doesn't divide). Works in cents. When every qty is a whole
    number the qtys are reduced by their common factor, so "2 kits" spreads per kit and comes
    out exact."""
    if not members:
        return []
    cents = int(round(float(target or 0) * 100))
    qtys = [float(m.qty or 0) for m in members]
    stds = [float(m.bundle_std_price if getattr(m, "bundle_std_price", None) is not None
                  else (m.unit_price or 0)) for m in members]
    weights = [max(s, 0) * q for s, q in zip(stds, qtys)]
    if sum(weights) <= 0:
        weights = [q for q in qtys]
    total_w = sum(weights) or 1.0
    ints = [_int_qty(q) for q in qtys]
    g = 0
    if all(i is not None and i > 0 for i in ints):
        for i in ints:
            g = gcd(g, i)
    if g > 1 and cents % g == 0:
        units, scaled = [i // g for i in ints], cents // g
    else:
        g, units, scaled = 1, qtys, cents
    # The remainder goes on the last line whose (reduced) qty is 1 and that carries weight;
    # failing that, the last line that carries weight; failing that, the last line.
    idx = list(range(len(members)))
    anchor = next((i for i in reversed(idx) if units[i] == 1 and weights[i] > 0), None)
    if anchor is None:
        anchor = next((i for i in reversed(idx) if weights[i] > 0), idx[-1])
    unit_cents = [0] * len(members)
    used = 0
    for i in idx:
        if i == anchor:
            continue
        u = round(scaled * weights[i] / total_w / units[i]) if units[i] else 0
        unit_cents[i] = u
        used += u * units[i]
    rest = scaled - used
    adjust = [0.0] * len(members)
    ua = units[anchor] or 1
    unit_cents[anchor] = round(rest / ua)
    leftover = rest - unit_cents[anchor] * ua          # in reduced units; 0 when it divides
    if leftover:
        adjust[anchor] = round(leftover * g) / 100.0
    return [(m, unit_cents[i] / 100.0, adjust[i]) for i, m in enumerate(members)]
