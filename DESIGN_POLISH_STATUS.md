# Design polish A–D: status

Updated: 2026-11-05 ~17:30 UTC (cloud session). Base branch: `roadmap/orderry-parity`. The PM session does the merges.
This branch only carries this file. It is a report, not something to merge.

## Blocker
**#122 (Phase 3 brand) is not merged yet** (merged 2026-11-05; see the update below). A is ready on `fix/design-polish-a`.
Its PR opens after #122 merges and A is rebased onto the beta branch. B is prepared on `fix/design-polish-b`
(on top of A for now), C on `fix/design-polish-c` (on top of B) and D on `fix/design-polish-d` (on top of C). Each PR opens after the one before it
merges, rebased onto the beta branch. No PRs are stacked.

## Update 2026-11-05 21:35 UTC: #122 merged; the beta branch moved past these branches
The beta branch is now `cb1e9e13`. Since these branches were made, it has gained:
- #144: the pre-Phase-2 page layouts restored, so the `lint:design` baseline is back to 969;
- #139: the audit's design-capture tooling (`scripts/design-capture/capture.mjs`);
- #153: A's contrast fixes, ported by the PM session onto the restored layouts, with my token values;
- #154 and #155: 112 hand-made buttons moved onto the shared Button.

So:
- **A: done on the beta branch through #153.** I'm not opening a PR for it; it would duplicate #153 and
  undo pages the PM session chose to keep.
- **B: partly done.** Its button work landed in #154 and #155. Not yet on the beta branch:
  - PO, new-PO and stock-take panels on the shared Drawer (Escape);
  - the Send menu off bold;
  - the Reports 44px phone row;
  - type hierarchy: settings title 16px → 14px, and 10 places still use CSS capitals.
- **C** (persistent frame and shared skeleton) and **D** (pixel screenshot checks): not on the beta branch.

B, C and D were built on the Phase-2 layouts and conflict with the restored ones, so each would be
rebuilt on the current beta branch, not rebased. I'm holding until the owner or PM decides:
1. rebuild B's remaining items, then C, then D, one PR at a time on the current beta branch; or
2. the PM session ports them itself, as it did with A; the branches are reference copies
   (`fix/design-polish-*-rebased`); or
3. drop the parts that no longer fit the restored layouts.

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
| C | `fix/design-polish-c` | ready; waiting on B | 0 → 0 | 0 → 0 | 42 → 42 | 0 → 0 | 34/34 | 760/760 | not run (no PR) |
| D | `fix/design-polish-d` | ready; waiting on C | 0 → 0 | 0 → 0 | 42 → 42 | 0 → 0 | 34/34 (+ visual 44/44) | 760/760 | not run (no PR) |

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

## Notes on C
- Shell is now a layout route (`AppFrame`), so the sidebar, header and badges stay mounted while pages
  change. Pages still name themselves with `<Shell title>`, and a Shell rendered on its own (as in
  the existing tests) still draws the full frame. No page files changed.
- Measured by clicking through all 17 sidebar links (desktop):
  - sidebar remounted: B 16 of 17 → C **0 of 17**;
  - header titles correct on every page; no page errors;
  - badge API calls: 16 + 16 → 17 + 17. That's the same per navigation; the extra call is the first
    page load, which this run counted.
- Navigation does what a remount used to do: open menus and panels close, and badges refresh once
  (the old number stays until the new one arrives, which is the end of the flicker).
- One `PageSkeleton` replaces the blank "Loading…" screen for a page or settings section that is
  still downloading.
- Tests: 3 new tests in `AppFrame.test.tsx` (vitest 760/760); tsc, lints and build pass.

## Notes on D
- `tests-e2e/visual.spec.js` takes one screenshot of each of 22 staff screens at 1440 and 390 and
  compares it with a committed baseline (44 PNGs, 3.5 MB).
- It's strict on purpose. I checked by changing the shared button's corners from 12px to 8px: 6–13
  pixels changed per screen, and the check failed. Unchanged reruns differ by 0 pixels, including
  against a freshly seeded database.
- Steady from day to day: fixed browser clock; dates, times and weekdays replaced with `##`; UTC; no
  animations. Playwright is pinned to 1.63.0 (it was `^1.45`, which pulled a new Chromium each run).
- E2E workflow: the visual check runs first, on the fresh seed. The tab-shift and PWA specs change
  data, for example a supplier appears in Contacts after tab-shift. On failure the run uploads diff
  images. "Run workflow" → "Record new visual baselines" records new ones as an artifact.
  `tests-e2e/README-visual.md` explains it.
- One shared sign-in for the specs (`admin-token.js`) keeps CI under the server's 10 sign-ins per
  5 minutes. tab-shift uses it too; its checks are unchanged.
- **Risk:** the baselines were recorded in this Linux container, not on GitHub's runner. If the
  first CI run differs only by font rendering, I'll record new ones with the workflow's button,
  commit them, and say so in the PR. D also has to be recorded again after A–C are rebased onto
  the beta branch, so its pictures match what's merged.
