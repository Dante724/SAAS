# Wire-Through Pass — Four stored-but-inert settings now enforce

Closes latent gaps where a setting existed (and the UI implied it worked) but no
code consumed it. Every change is **strictly additive / off-by-default**, so
existing installs behave identically until the setting is turned on.

## Item 1 — `asset_one_open_ticket`
Order creation now blocks (409) adding an asset that's already on another
**open** (non-terminal) ticket, when the org enables the flag. Join is on
`Order.status → OrderStatus.key` with an outer join so status-less/estimate
orders don't false-trigger. Estimates are exempt. OFF by default → duplicates
allowed as before.

## Item 2 — role `location_scope`
New `check_location_access(db, current, location_id)` in `services/auth.py`:
a role with `location_scope="selected"` may only act on locations in its
`location_ids`; unscoped roles and owners (no role) are unaffected. Enforced at
order creation (403 out-of-scope). Deliberately a **separate helper**, not folded
into `require_perm`, since location scope gates *where* you act, not *what*.

## Item 3 — `rounding_enabled`
`_recalc_order` rounds the order total to the nearest 0.05 when enabled. OFF by
default → exact totals unchanged (proven by test: 10.02 stays 10.02 off, becomes
10.00 on).

## Item 4 — `warranty_count_from`
The close-time warranty activation honors the org policy: `closure` (default,
from `closed_at`) or `created` (from the order's `created_at`). Test proves a
100-day-old order with 30-day warranty lands in the future under `closure` and in
the past under `created`.

## Tests
`backend/tests/test_wire_through.py` (5): asset-guard off→allow / on→409,
location-scope 403 out-of-scope + allow in-scope, unscoped-role unaffected,
rounding off/on, warranty closure-vs-created. Full suite 98.

## Process note
Both initial test failures were harness bugs (Employee uses `token_version` not
`tv`; the test session needed `expire_all()` to see the API's committed write) —
the implementation was correct on first run, verified by a direct repro. No
`--unsafe-fixes` were applied (they silently broke a SQLAlchemy filter in a prior
pass). No new tables/columns — logic-only over existing P4 fields; schema stays 94.
