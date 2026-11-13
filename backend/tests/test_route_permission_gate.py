"""CI gate: every write route must carry a role permission, or be a reviewed exception.

The audit found 451 routes guarded only by "is logged in" — role settings did nothing for most
writes. This test fails when a new POST/PUT/PATCH/DELETE route is added without
`require_perm(...)` (or platform-admin auth), unless it is listed in
ungated_write_routes.txt with the reason it's open to every staff member (or to the public).
It also fails when a listed route has since been gated, so the list only ever shrinks.

To fix a failure: add `Depends(require_perm("<code>"))` (codes live in
backend/services/permissions.py CATALOG), or — if the action genuinely belongs to every
employee — add the printed line to ungated_write_routes.txt with `# <reason>` (staff routes must
say why they are safe open: the inline check that guards them, or why every employee may do it).
"""
import inspect
import re
import sys
from pathlib import Path

from fastapi.routing import APIRoute

from main import app

ALLOWLIST = Path(__file__).with_name("ungated_write_routes.txt")


def _walk(routes):
    for r in routes:
        if isinstance(r, APIRoute):
            yield r
        elif hasattr(r, "original_router"):        # FastAPI >= 0.13x wraps included routers
            yield from _walk(r.original_router.routes)
        elif hasattr(r, "routes"):
            yield from _walk(r.routes)


def _dep_names(dependant, out):
    for sub in dependant.dependencies:
        out.add(getattr(sub.call, "__qualname__", repr(sub.call)))
        _dep_names(sub, out)
    return out


def _ungated_write_routes() -> set[str]:
    found = set()
    for r in _walk(app.routes):
        names = _dep_names(r.dependant, set())
        if any("require_perm" in n for n in names):
            continue
        if any("platform" in n.lower() for n in names):      # platform-admin console has its own auth
            continue
        kind = "staff" if "get_current_employee" in names else "public"
        for m in sorted(r.methods - {"GET", "HEAD", "OPTIONS"}):
            found.add(f"{m} {r.path} [{kind}]")
    return found


def _allowlisted() -> set[str]:
    lines = ALLOWLIST.read_text().splitlines()
    return {ln.split("#", 1)[0].strip() for ln in lines if ln.split("#", 1)[0].strip()}


def test_no_new_ungated_write_routes():
    new = sorted(_ungated_write_routes() - _allowlisted())
    assert not new, ("Write routes without a role permission (gate them with require_perm, or "
                     "allowlist with a reason in ungated_write_routes.txt):\n  " + "\n  ".join(new))


def test_every_staff_exception_says_why():
    """Audit SEC-06: a signed-in staff write left open needs the reason it is open (the inline
    check that guards it, or why every employee may do it). A bare line was how 130 routes ended
    up default-allow for any active employee, roleless invitees included."""
    bare = sorted(ln.strip() for ln in ALLOWLIST.read_text().splitlines()
                  if "[staff]" in ln.split("#", 1)[0] and not ln.partition("#")[2].strip())
    assert not bare, ("Staff write routes allowlisted without a reason. Gate them with "
                      "require_perm, or add `# <why it is safe open>` to each line:\n  " + "\n  ".join(bare))


def test_allowlist_has_no_stale_entries():
    stale = sorted(_allowlisted() - _ungated_write_routes())
    assert not stale, ("These allowlisted routes are now gated or gone — delete them from "
                       "ungated_write_routes.txt:\n  " + "\n  ".join(stale))


# ── Re-review N5: a reason must name a check that exists ─────────────────────────────────────────
# A staff exception's reason is either "open by nature" (the caller's own data, or nothing saved),
# or it names the inline check that guards the route. Named checks are verified as far as text can
# be: each function name (`_guard_order_access`, `assert_perm`, `check_location_access`, ...) and
# each permission code (`can_*`) it names must appear in the route's own module, its dependencies,
# or a module it names (`importer.assert_access`); a permission code must be a real catalogue code.
_OPEN_BY_NATURE = ("self-service", "saves nothing", "nothing saved", "writes nothing", "renders only",
                   "read-only", "returned to the caller", "returned for review", "by design", "own ")
_CHECK_WORDS = ("guard", "assert", "check", "perm", "scope", "quota", "verify", "sees_", "involves_",
                "_may_", "_load", "_can_")
_IDENT = re.compile(r"\b(?:([a-z_][a-z0-9_]*)\.)?([a-z_][a-z0-9_]*)\b")


def _named_checks(reason: str) -> list[tuple[str, str]]:
    """(module hint or '', identifier) for every code-looking name in the reason."""
    return [(mod, name) for mod, name in _IDENT.findall(reason)
            if name.startswith("can_") or ("_" in name and any(w in name for w in _CHECK_WORDS))]


def _source(mod) -> str:
    try:
        return inspect.getsource(mod) if mod else ""
    except (OSError, TypeError):
        return ""


def _route_sources() -> dict:
    """'METHOD path' -> text the route's checks can live in: its module's source and its deps."""
    out = {}
    for r in _walk(app.routes):
        text = _source(sys.modules.get(r.endpoint.__module__)) + "\n" + " ".join(_dep_names(r.dependant, set()))
        for m in r.methods - {"GET", "HEAD", "OPTIONS"}:
            out[f"{m} {r.path}"] = text
    return out


def test_every_staff_exception_reason_names_a_real_check():
    from backend.services.permissions import ALL_CODES
    sources = _route_sources()
    bad = []
    for ln in ALLOWLIST.read_text().splitlines():
        entry, _, reason = ln.partition("#")
        if "[staff]" not in entry:
            continue
        key = entry.split("[", 1)[0].strip()
        named = _named_checks(reason)
        if not named:
            if not any(w in reason.lower() for w in _OPEN_BY_NATURE):
                bad.append(f"{key}: names no inline check, and isn't the caller's own data or read-only")
            continue
        for mod, name in named:
            if name.startswith("can_") and name not in ALL_CODES:
                bad.append(f"{key}: {name} is not a permission code")
                continue
            where = sources.get(key, "")
            if mod:
                where += _source(next((v for k, v in list(sys.modules.items())
                                       if k == mod or k.endswith(f".{mod}")), None))
            if name not in where:
                bad.append(f"{key}: names {name}, which its module never uses")
    assert not bad, ("Allow-list reasons that don't match the code (fix the reason, or gate the route):\n  "
                     + "\n  ".join(bad))
