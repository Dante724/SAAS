"""CI gate: every write route must carry a role permission, or be a reviewed exception.

The audit found 451 routes guarded only by "is logged in" — role settings did nothing for most
writes. This test fails when a new POST/PUT/PATCH/DELETE route is added without
`require_perm(...)` (or platform-admin auth), unless it is listed in
ungated_write_routes.txt with the reason it's open to every staff member (or to the public).
It also fails when a listed route has since been gated, so the list only ever shrinks.

To fix a failure: add `Depends(require_perm("<code>"))` (codes live in
backend/services/permissions.py CATALOG), or — if the action genuinely belongs to every
employee — add the printed line to ungated_write_routes.txt.
"""
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


def test_allowlist_has_no_stale_entries():
    stale = sorted(_allowlisted() - _ungated_write_routes())
    assert not stale, ("These allowlisted routes are now gated or gone — delete them from "
                       "ungated_write_routes.txt:\n  " + "\n  ".join(stale))
