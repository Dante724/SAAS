"""The role editor tells the truth: every permission the backend checks is in the catalogue
(so it can be switched off), and the "coming soon" list is exactly the catalogue codes that
nothing checks yet. Wiring a permission up means removing it from NOT_YET_ENFORCED."""
import pathlib
import re

from backend.services.permissions import ALL_CODES, NOT_YET_ENFORCED

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


def test_coming_soon_list_matches_what_nothing_checks():
    src = _sources()
    unchecked = {c for c in ALL_CODES if not _mentioned(c, src)}
    assert NOT_YET_ENFORCED <= ALL_CODES
    assert unchecked == NOT_YET_ENFORCED, (
        f"newly wired (remove from NOT_YET_ENFORCED): {sorted(NOT_YET_ENFORCED - unchecked)}; "
        f"unchecked but not listed: {sorted(unchecked - NOT_YET_ENFORCED)}")


def test_every_checked_permission_is_in_the_catalogue():
    src = _sources()
    used = set()
    for t in src.values():
        used |= set(re.findall(r"(?:require_perm|has_perm|assert_perm|role_allows)\([^)]*?[\"'](can_[a-z_]+)[\"']", t))
        used |= set(re.findall(r"perms\.get\([\"'](can_[a-z_]+)[\"']", t))
    missing = used - ALL_CODES
    assert not missing, f"checked in code but not in the role editor: {sorted(missing)}"
