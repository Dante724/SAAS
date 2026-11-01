"""The role editor tells the truth: every permission the backend checks is in the catalogue
(so it can be switched off), and every catalogue code is checked somewhere — nothing in the
role editor is a switch that does nothing."""
import pathlib
import re

from backend.services.permissions import ALL_CODES

BACKEND = pathlib.Path(__file__).resolve().parents[1]


def _sources() -> dict[str, str]:
    out = {}
    for f in BACKEND.rglob("*.py"):
        rel = f.relative_to(BACKEND).as_posix()
        if rel.startswith(("tests/", "migrations/")) or rel == "services/permissions.py" or "__pycache__" in rel:
            continue
        out[rel] = f.read_text()
    return out


def _mentioned(code: str, src: dict[str, str]) -> bool:
    pat = re.compile(r"[\"']" + re.escape(code) + r"[\"']")
    return any(pat.search(t) for t in src.values())


def test_every_catalogue_permission_is_checked():
    src = _sources()
    unchecked = {c for c in ALL_CODES if not _mentioned(c, src)}
    assert not unchecked, f"in the role editor but nothing checks it: {sorted(unchecked)}"


def test_every_checked_permission_is_in_the_catalogue():
    src = _sources()
    used = set()
    for t in src.values():
        used |= set(re.findall(r"(?:require_perm|has_perm|assert_perm|role_allows)\([^)]*?[\"'](can_[a-z_]+)[\"']", t))
        used |= set(re.findall(r"perms\.get\([\"'](can_[a-z_]+)[\"']", t))
    missing = used - ALL_CODES
    assert not missing, f"checked in code but not in the role editor: {sorted(missing)}"
