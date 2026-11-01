# Tekly — Roadmap to Parity + Beyond Orderry

_Compiled from this session's audits: unexposed backend, legacy→React functional gaps,
and the "beat Orderry" strategy. Priority = value × (1 / effort × risk)._

Legend: **BE?** = backend already exists (frontend-only work) · **FE-only** = ships live without
a server restart · **NEW BE** = needs backend + migration (server restart to go live).

---

## Phase 0 — Cleanup (zero risk, do anytime)

- Delete `_design-rebuild-safe/` (5.9 MB backup), `frontend/assets/*.js` (49 orphaned bundles),
  `legacy.html` / `book.html` / `track.html`, `frontend/tests/*.test.js` (97 files),
  `static/js/` **except** `core.js` / `icons.js` / `pwa.js` / `tech/*`, and React
  `ProductDrawer.tsx` + `WorkOrdersPreview.tsx`. ~49k dead JS lines.
- Keep: `admin.html`, `tech.html`, `pay/portal/offline`, `sw.js`, `manifest`, `frontend/index.html` (fallback).

## Phase 1 — Wire what's already built (FE-only, highest ROI) ← _executing tonight_

Backend is done; the React UI just never called it. These ship live immediately.

- **A. Asset catalog & identifier-rule admin** (Settings) — `/settings/asset-brands|groups|models|asset-identifier-rules`. The cockpit for the multi-industry engine we just built. **Strategic keystone.**
- **B. Manual stock adjust + batch/FEFO view** (InventoryDrawer) — `/inventory/parts/:id/adjust`, `/batches`.
- **C. Fleet & owner analytics** (Reports) — `/analytics/fleet`, `technician-productivity`, `location-comparison`, `trends`, `business-insights`. B2B differentiator.
- **D. Loyalty / coupon / gift-card redemption at POS + WO** — `/orders/:id/redeem-loyalty|apply-coupon|redeem-giftcard`. Revenue/retention.
- **E. Supplier ledger + price-list import** — `/suppliers/:id/ledger`, `/import-price-list`.
- **F. Special-order parts loop** (WO) — `/orders/:id/create-po`, `/orders/:id/lines/:id/receive-stock`.
- **G. Archived orders view + bulk order actions** — `/orders/archived`, `/orders/bulk`.
- **H. AI call insights** (telephony) — `/comms/calls/insights`, `/comms/calls/:id/ai`.

## Phase 2 — Depth ports (medium effort)

- **Scanning / barcode workflows** — `/inventory/scan`, `/assets/scan-resolve`, transfer/stocktake scan. Keyboard-wedge first (works with USB scanners), camera later. _Shop-floor essential; Orderry leans on this._
- **Deep stocktake count-sheet** — `/stocktake/:id/lines|scan|import|post|settlement` (React has only "start").
- **Conversions depth** — `/conversions/prefill/*`, `asset↔product` explicit flows.
- **Manual stock adjust batches → FEFO picking** on WO/POS lines.
- **Webhook delivery log**, **generate-serial**, **inbound email-accounts** settings surface.

## Phase 3 — Regressions the React rewrite dropped (defensive, larger)

- **i18n full body port** — React i18n is a 32-line shell stub; legacy had a 287-line dictionary (ES + RTL-ready). Port the dictionary + wrap app strings. **Critical for LATAM/Europe/Asia.**
- **PWA / offline / web push** — React has no service worker or manifest; push code calls `serviceWorker.ready` that never resolves. Restore installability + offline (legacy `sw.js` is the template).
- **Technician app** — rebuild `tech.html` (`/tech`) as a React shop-floor view. Net-new competitive vs Orderry's tech app.
- **Platform-admin console** — port `admin.html` (tenant/subscription/suspension) into React or formally keep it separate.

## Phase 4 — "Better than Orderry" wedges (net-new, product bets)

- **Same-day onboarding + AI-assisted migration** — import a shop's old export/spreadsheet/pricebook photos → live in a day. Wins evals.
- **Agentic shop assistant** — turn system-of-record into system-of-action: auto-draft follow-ups, flag stalled jobs, propose upsells from checklist fails, chase reviews, spot rebook-due clients. Reuses recommendations + notifications + AI + fleet data.
- **Multi-trade platform positioning** — one tenant, many trades under the template/identifier engine (franchises, mixed shops).

---

## Sequencing rationale

Phase 1 is pure leverage — value already paid for, only wiring remains, and it ships without a
backend restart. Phase 3 items are regressions that directly undercut the global/multi-business
strategy, so they rank above net-new bets. Phase 4 is where you actually pass Orderry rather than
match it — but only lands once onboarding (migration) stops killing evals.

**Standing engineering rules** (per this session): React/Tailwind idiom app-wide, nothing left
unwired, `tsc` + build + deploy + live cache-bust check + a test + CHANGELOG per batch, verify with
the 3 skills (tech-PM, senior-architect-QA, superpowers).

> Note: backend (`.py`) changes require restarting the dev server (`make run`) to go live; the
> frontend-only Phase 1 items do not.
