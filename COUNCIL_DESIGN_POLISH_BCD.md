# Council note: design polish B, C and D after the layout restore (5 Nov 2026)

Beta branch `roadmap/orderry-parity` at cb1e9e13. Three independent reviewers (tech-PM,
architect/engineer, QA/design) each read the code and git history read-only, from the same brief.
Reference branches: `fix/design-polish-{a,b,c,d}-rebased`, built on the Phase-2 layouts.

## What the restore (#144) means
The owner rejected the Phase-2 *look*, not polish. The rule now: fix behaviour, accessibility and
stability; don't restyle the restored pages. #153 (A, ported by the PM session) already works this way.

## Facts checked
- `frontend-react/src/components/SettingsPage.tsx` is imported nowhere on beta, so B's
  settings-title change would do nothing.
- `pages/reports/ReportsToolbar.tsx` no longer exists; #144 deleted it.
- #144 cut shared `Drawer` use to 2 files (admin). The restored PO panel is 720px wide with an icon tile
  in its header, a size and look the shared Drawer (440/560/840) doesn't offer.
- The PO, new-PO and stock-take panels still don't close on Escape.
- `SendMenu.tsx` is still `font-bold`, with a non-standard height.
- `uppercase` appears 17 times on beta, and some are print CSS or badges.
- C: `App.tsx` is the same as C's base. `Shell.tsx` differs by 4 lines. The only conflict is one line in
  SettingsHub. All 21 routes render exactly one `<Shell>`.
- D merges cleanly, but all 44 PNG baselines show Phase-2 screens.

## Verdicts
| Item | tech-PM | architect | QA/design | Council |
|---|---|---|---|---|
| B1 inventory panels | move to Drawer (PM session) | **Escape only**, keep the restored panels | move to Drawer | **Add Escape-to-close to the PO, new-PO and stock-take panels; change nothing else** (2 of 3 want Escape; moving to Drawer would undo the restored width and header) |
| B2 Send menu | keep (PM session) | keep, cherry-pick | keep | **Keep**: semibold, shared height |
| B3 Reports phone row | drop | drop | rebuild in ReportsHub | **Drop.** Re-raise only if a 390px capture shows tap targets under 44px |
| B4 type hierarchy | hold, ask owner | keep | keep | **Settings title: moot** (file unused). **Uppercase section labels: ask the owner one line**, since they may be part of the restored look |
| C persistent frame | keep, cloud session | keep, near-clean cherry-pick | keep, cloud session | **Keep, cloud session**, near-clean cherry-pick |
| D pixel guard | keep, after C, new baselines | keep harness, new baselines | keep, after C, new baselines | **Keep, after C**: reuse the harness, record every baseline fresh on the final beta |

## Risks to watch
- **B1:** Escape in a panel opened over another must close only the top one, the way the shared Drawer
  does it.
- **C:** badges refetch on page change, as before. A `?section=` change didn't remount Shell before
  either, so that is unchanged. Also check the MFA gate, `applyDir` and `liveRef` with a frame that never
  unmounts.
- **D — 0-pixel tolerance:** it held across local reruns, but CI's runner is new to it.
- **D — Playwright pin:** pinning 1.63.0 changes the Chromium the existing specs run on.
- **D — shared token helper:** changes how tab-shift signs in; its checks are unchanged.
- **D — CI time:** 44 more screenshots per run.

## Proof for each
- **B1:** a vitest per panel showing Escape closes it, with before/after captures identical.
- **B2:** 36px (32px small) and semibold; tab-shift stays 34/34.
- **C:** `AppFrame.test.tsx`, plus a navigation check: the sidebar node is never remounted, and exactly
  one unread and one walk-in request go out per page change.
- **D:** two CI runs in a row green with no changes, and a deliberate 1px change fails with diff images
  uploaded.

## Who and in what order
One session works the files, so the two don't collide. The cloud session ships three small PRs on the
current beta, one after another: **B′** (Escape on the 3 panels, plus the Send menu; plus uppercase
labels only if the owner says so), then **C**, then **D** with fresh baselines. The `-rebased`
branches stay as reference only.
