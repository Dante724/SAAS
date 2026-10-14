"""144jt — parts↔device compatibility (repair-native inventory).

A part's `compatibility_json` may carry a `"fits"` list describing which devices it
fits, e.g. {"fits": [{"brand": "Apple", "model": "iPhone 13"}, {"brand": "Apple"}]}.
An entry with only a brand matches any model of that brand; an entry with brand +
model must match both. Matching is case-insensitive and forgiving of spacing.

Pure and dependency-free so it's unit-testable and safe to call in hot paths. Parts
with no `fits` data are simply "not a known fit" (False) — never an error, and other
keys in compatibility_json (e.g. conversions' `from_asset`) are left untouched.
"""
from typing import Optional


def _norm(s) -> str:
    return " ".join(str(s or "").strip().lower().split())


def fits_list(compat) -> list:
    """The device-compatibility entries, tolerant of shape (dict with 'fits', or a
    bare list). Anything else → empty."""
    if isinstance(compat, dict):
        f = compat.get("fits")
        return f if isinstance(f, list) else []
    if isinstance(compat, list):
        return compat
    return []


def part_fits(compat, brand: Optional[str], model: Optional[str]) -> bool:
    """True iff the part explicitly lists a device matching this brand (+ model if the
    entry specifies one). No device brand, or no fits data → False."""
    b, m = _norm(brand), _norm(model)
    if not b:
        return False
    for e in fits_list(compat):
        if not isinstance(e, dict):
            # allow bare strings like "iPhone 13" — treat as a model or brand token match
            if _norm(e) in (b, m) or (m and _norm(e) == m):
                return True
            continue
        eb, em = _norm(e.get("brand")), _norm(e.get("model"))
        if eb and eb != b:
            continue
        if em and em != m:
            continue
        if eb or em:            # at least one field specified and all specified fields matched
            return True
    return False
