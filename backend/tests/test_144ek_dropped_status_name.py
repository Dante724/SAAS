"""144ek — the default WO status in the terminal `dropped` group must not be named
"Dropped off" (reads like intake). It is the cancelled/abandoned/not-collected terminal
state, so it must carry a name that can't be mistaken for a fresh drop-off."""
from backend.routers.statuses import DEFAULT_STATUSES, TERMINAL_GROUPS


def _row(key):
    return next(r for r in DEFAULT_STATUSES if r[0] == key)


def test_dropped_status_is_terminal_and_not_named_like_intake():
    key, name, group, *_rest = _row("dropped")
    assert group == "dropped" and group in TERMINAL_GROUPS   # still the terminal bucket
    assert name != "Dropped off"                              # the bug: intake-sounding name
    assert name == "Not collected"


def test_new_is_the_only_intake_default():
    # guard that the early/intake default is "new" (group new), so nothing terminal looks like intake
    key, name, group, *_ = _row("new")
    assert group == "new" and name == "New"
