"""Every instant leaves the API with a "Z" (launch recheck B6).

Instants (created_at, opened_at, an activity row's "at") are stored as naive UTC. Sent as a bare
"2026-11-11T07:14:43" a browser reads them as its own local time, so an IST or New York cashier saw
shift and activity times 5-5.5 hours off. Most routers build their rows by hand with .isoformat(),
so instead of fixing ~80 call sites one by one (and the next one someone writes) the app's default
response marks them here, by key: "*_at", "at" and "ts" are instants.

Shop wall-clock times stay bare, because they are read as the shop's own clock: scheduled_for /
scheduled_to (no "_at") and the "*_at" keys in WALL_CLOCK below (due times, and promo / PO dates
typed into a datetime-local field). Values that already carry a zone, and bare dates, are left alone.
"""
import re

from backend.core.finite_numbers import FiniteJSONResponse

# "*_at" keys that hold a shop wall-clock time, not an instant.
WALL_CLOCK = frozenset({
    "due_at",                  # orders and tasks: the shop's clock (dates.local_now)
    "starts_at", "ends_at",    # promo windows and booking ends, typed as local time
    "expected_at",             # purchase order expected delivery, typed as local time
})
_EXTRA = frozenset({"at", "ts"})
_BARE = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d(:\d\d(\.\d{1,6})?)?")


def is_instant_key(key) -> bool:
    return isinstance(key, str) and (key in _EXTRA or key.endswith("_at")) and key not in WALL_CLOCK


def mark_utc(v):
    """A copy of a JSON-ready payload with "Z" added to every bare instant."""
    if isinstance(v, dict):
        return {k: (x + "Z" if isinstance(x, str) and is_instant_key(k) and _BARE.fullmatch(x)
                    else mark_utc(x)) for k, x in v.items()}
    if isinstance(v, list):
        return [mark_utc(x) for x in v]
    return v


class ApiJSONResponse(FiniteJSONResponse):
    """The app's default response: FiniteJSONResponse (B9) plus UTC-marked instants (B6)."""

    def render(self, content) -> bytes:
        return super().render(mark_utc(content))
