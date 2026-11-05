# Design polish A–D: status

Updated: 2026-11-05 ~17:30 UTC (cloud session). Base branch: `roadmap/orderry-parity`. The PM session does the merges.
This branch only carries this file. It is a report, not something to merge.

## Blocker
**#122 (Phase 3 brand) is not merged yet** (checked 2026-11-05 ~17:30 UTC). A is ready on `fix/design-polish-a`.
Its PR opens after #122 merges and A is rebased onto the beta branch. B is prepared on `fix/design-polish-b`
(on top of A for now); its PR opens after A merges, rebased onto the beta branch. No PRs are stacked.

## How it's measured
`scripts/design-capture/capture.js` (added in A, with a README) checks every staff screen and tab, plus a
record drawer and each of its tabs on Work orders, Inventory, Contacts and Invoices (about 104 states), in
light and dark, at 1440 and 390. A "before" is the previous step built and served on :8111.

Note: the first A numbers posted here (116 → 0, 94 → 0, 369 buttons) were from an earlier version of the
tool. That version counted the header search box once per screen and called pills and fields buttons.
The numbers below replace them.

| PR | Branch | State | Contrast misses, light | Contrast misses, dark | Off-design buttons | Sideways scroll (phone) | Tab-shift | vitest | CI |
|---|---|---|---|---|---|---|---|---|---|
| A | `fix/design-polish-a` | ready; waiting on #122 | 38 → **0** | 5 → **0** | 49 → 52 (see note) | 0 → 0 | 34/34 | 756/756 | not run (no PR) |
| B | `fix/design-polish-b` | ready; waiting on A | 0 → 0 | 0 → 0 | 49 → **42** | 0 → 0 | 34/34 | 757/757 | not run (no PR) |
| C | — | not started | | | | | | | |
| D | — | not started | | | | | | | |

A's +3 buttons come from demo data: those job lines only rendered in the after run's database. B fixes them.

## Notes on A
- A builds on the local session's WIP commit 60021f4 and keeps all of it:
  - light tint text in the 500/600 shades renders at 700, set at the token level, so no test changes;
  - the placeholder token is 4.5:1.
- A adds:
  - dark grey tuning;
  - words moved off the placeholder grey;
  - `aria-disabled` on greyed-out setting rows;
  - an always-light preview of the booking page;
  - status pills readable in light mode ("Done" was 1.9:1);
  - the last `lint:design` patterns removed (the Plans drawer now uses the shared Drawer).
- Checks: no test files changed. tsc, lint:uitext and build pass.
- `lint:design` is 0 in A's own files. On today's beta branch the baseline still holds 221 patterns,
  all in the admin and customer-page files that #122 rewrites. After the rebase onto #122 the baseline locks at **0**.

## Notes on B
- Work-order drawer and its sections: the hand-styled action and icon buttons use the shared look.
  Toggles, status pills, inline row editors and text links stay as they are.
- The purchase order, new purchase order and stock-take panels use the shared Drawer, so Escape works.
  The Drawer gets an optional header `actions` slot.
- The Send and Print menus are now one split button on the shared look: 32/36px, semibold.
- Reports on a phone: the Technician and Period filters get their own 44px row, and the title below
  stays at the same position on both tabs.
- Type: settings section and summary titles are 14px, below the 16px page title. Section labels lose
  CSS capitals, and report group names show in sentence case through a new tested `sentenceCase` helper.
