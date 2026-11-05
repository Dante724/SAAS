# Design polish A–D: status

Updated: 2026-11-05 (cloud session). Base branch: `roadmap/orderry-parity`. The PM session does the merges.
This branch only carries this file. It is a report, not something to merge.

## Blocker
**#122 (Phase 3 brand) is not merged yet.** A is ready on `fix/design-polish-a`. Its PR opens after #122
merges and A is rebased onto the beta branch. Each PR is based on the beta branch; none are stacked.

## How it's measured
`scripts/design-capture/capture.js` (added in A, with a README) checks 90 screens and tabs, in light
and dark, at 1440 and 390 widths. "Before" is #122's head (33670e3), built and served on :8111.

| PR | Branch | State | Contrast misses, light | Contrast misses, dark | Off-design buttons | Sideways scroll (phone) | Tab-shift | CI |
|---|---|---|---|---|---|---|---|---|
| A | `fix/design-polish-a` | ready; waiting on #122 | 116 → **0** | 94 → **0** | 369 → 368 | 0 → 0 | 34/34 | not run yet (no PR) |
| B | — | not started | | | | | | |
| C | — | not started | | | | | | |
| D | — | not started | | | | | | |

## Notes on A
- A builds on the local session's WIP commit 60021f4 and keeps all of it:
  - light tint text in the 500/600 shades renders at 700, set at the token level, so no test changes;
  - the placeholder token is 4.5:1.
- A adds:
  - dark grey tuning;
  - words moved off the placeholder grey;
  - `aria-disabled` on greyed-out setting rows;
  - an always-light preview of the booking page;
  - the last `lint:design` patterns removed (the Plans drawer now uses the shared Drawer).
- Checks: vitest 756/756 with no test files changed. tsc, lint:uitext and build pass.
- `lint:design` is 0 in A's own files. On today's beta branch the baseline still holds 221 patterns,
  all in the admin and customer-page files that #122 rewrites. After the rebase onto #122 the baseline locks at **0**.
