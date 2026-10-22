"""Shared list-page bounds (batch 2, Oct 2026 audit).

Every staff list serves at most PAGE_MAX rows per request. A larger `limit` is clamped
rather than rejected: older bundles asked for limit=1000 and paged in the browser, and a
422 there renders the whole page as an error (see test_144ay). The response's `total` tells
such a caller there is more. Values above LIMIT_CEIL are still a validation error."""
from fastapi import Query

PAGE_MAX = 200
LIMIT_CEIL = 1000


def limit_query(default: int = 50):
    return Query(default, ge=1, le=LIMIT_CEIL)


def skip_query():
    return Query(0, ge=0)


def clamp(limit: int, cap: int = PAGE_MAX) -> int:
    return max(1, min(int(limit), cap))
