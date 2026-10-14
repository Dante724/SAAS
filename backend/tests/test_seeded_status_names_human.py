"""Every seeded status NAME (the text users see) must be a human label — never a raw key.
A key is a slug (snake_case, shown separately in mono); the name must not contain an
underscore and must not just be the key. Guards the whole status catalog, all pipelines,
against the "Dropped off / waiting_parts-as-a-label" class of mistake."""
import pytest

from backend.routers.statuses import (
    DEFAULT_STATUSES,
    ESTIMATE_STATUSES,
    RECORD_TYPE_DEFAULTS,
)

# (key, name) pairs across every seeded pipeline
PAIRS = (
    [(r[0], r[1]) for r in DEFAULT_STATUSES]
    + [(r[0], r[1]) for r in ESTIMATE_STATUSES]
    + [(r[0], r[1]) for rows in RECORD_TYPE_DEFAULTS.values() for r in rows]
)


@pytest.mark.parametrize("key,name", PAIRS, ids=[k for k, _ in PAIRS])
def test_status_name_is_a_human_label(key, name):
    assert "_" not in name, f"status '{key}' shows a raw key as its name: {name!r}"
    assert name != key, f"status '{key}' name is just the key, not a label"
    assert name[:1].isupper(), f"status '{key}' name should read like a label: {name!r}"
