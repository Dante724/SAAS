# Spec — Smart Work Order: two differentiators beyond Orderry

Status: Draft · Owner: Chomu · Target build: 144gn+
Scope: two features that turn the RepairOS work order from a record-keeper into a system that *acts*. Both are wired into surfaces that already exist (checklist, Invoices tab, status graph); neither redesigns the WO or duplicates backend/auth.

---

## Feature 1 — Checklist that pays for itself (auto-drafted upsells)

### Problem statement
Techs run a diagnostic checklist on nearly every job, but a failed or out-of-spec item (battery at 71%, worn brake pads, cracked back glass) dies as a tick in a form. Turning that finding into a customer quote is a manual, easily-skipped step, so shops leave attach revenue on the table on the exact jobs where the customer is most receptive. Orderry's checklist is a static form with no path from a finding to an estimate line — a checklist that drafts the upsell for the tech is a capability Orderry cannot match.

### Goals
- Convert diagnostic findings into revenue without adding tech steps: a Fail/threshold breach auto-drafts a *pending* line the tech accepts or dismisses in one tap.
- Increase attach rate on jobs with a failed check (target below), measured against jobs with the same failed check before launch.
- Keep the tech in control — the software surfaces, it never charges or sends on its own.

### Non-goals
- No auto-charging and no auto-send to the customer. Every money/comms action stays an explicit tap. (Trust + our own action-safety rules.)
- No new pricing engine. The drafted line reuses the existing catalog price / service price. Threshold-to-product mapping is config, not logic.
- No ML/またprediction of *what* to upsell. v1 is deterministic: a checklist item is configured with a linked product/service and (for numeric items) a threshold. Out of scope: recommending unrelated products.
- Not a replacement for the existing Recommendations feature — this *feeds* it (see Requirements P0-4).

### User stories
- As a technician, when I mark a checklist item Fail (or enter a number past its threshold), I want a draft upsell line to appear on the order automatically so that I don't have to build the quote by hand.
- As a technician, I want to dismiss a drafted line in one tap so that irrelevant suggestions don't clutter the order or pressure me.
- As a service advisor, I want a one-tap "text quote to customer" on the drafted line so that I can get approval while the customer is still thinking about it.
- As a shop owner, I want to configure which checklist items map to which product/service (and the threshold) so that the automation reflects how my shop actually sells.
- As a shop owner, I want the drafted line to route through the existing Recommendations/approval flow so that approvals, commission, and the customer portal keep working unchanged.

### Requirements

**P0 — Must have**
- P0-1 · Template config: a checklist template item gains an optional `linked_catalog_id` (product or service) and, for numeric/`check_result` items, a `threshold` + comparator (e.g. `< 80`). Migration adds nullable columns to `checklist_template_items`. Acceptance: given an item configured with a linked product and threshold, when a tech records a value past the threshold, then the system flags the item as an upsell trigger.
- P0-2 · Trigger on fill: when an `order_checklist_item` is saved with result = Fail (for `affects_result` items) or a numeric value crossing the threshold, the backend creates a *pending* recommendation (reuse existing `Recommendation` model, `source = "checklist"`, link to the checklist item). Idempotent: re-saving the same item does not create duplicates. Acceptance: Given/When/Then per above; and re-saving the item a second time does not add a second draft.
- P0-3 · WO surface: the drafted line appears in the WO under the existing Recommendations sub-section (Services & Products) AND is visually attributed to the checklist ("from checklist: Battery health 71%"). Tech can Accept (moves to order lines via existing recs→invoice path) or Dismiss (soft-deletes the draft). Acceptance: accept produces an order line identical to a manually-added rec; dismiss removes it and does not re-trigger on the same saved value.
- P0-4 · Reuse approval/commission: accepted lines flow through the existing recommendation→invoice→approval→commission machinery with no new code path. Acceptance: an accepted checklist upsell that the customer approves pays commission exactly as a manual rec does (covered by existing tests + one new integration test).
- P0-5 · Permission gate: honoring existing `can_publish_recommend` / recommendation perms — a tech without the perm sees the draft but cannot send to customer. Acceptance: perm-less user cannot trigger "text quote".

**P1 — Nice to have**
- P1-1 · "Text quote to customer" one-tap action on the drafted line (SMS/email via existing comms), pre-filled with the line + price. Requires explicit tap; never automatic.
- P1-2 · Client-visible framing on the track page: approved checklist upsells show the finding ("battery tested at 71%") so the quote reads as thoroughness, not a sales pitch.
- P1-3 · Owner report: attach rate from checklist upsells vs manual, per tech, in existing Reports.

**P2 — Future**
- P2-1 · Suggested price/bundle logic (e.g. battery + labor as a package).
- P2-2 · Photo attach on the finding (reuse asset image field) so the customer sees the worn part.

### Success metrics
- Leading (30 days): ≥60% of failed/threshold checklist items with a configured linked product produce a draft the tech *keeps* (accept or leave on order) rather than dismiss — proves relevance, not nagging. Dismiss-rate above 60% is a signal the mapping is wrong, revisit.
- Leading: median taps from finding → quote sent drops from (manual baseline, ~measure) to 2.
- Lagging (quarter): attach revenue on jobs with a failed check up vs pre-launch cohort; target +X% (set baseline first).
- Guardrail: no increase in tech-reported "software nags me" — sample via the same channel used for other feedback.

---

## Feature 2 — Predicted ready-time + stall detection

### Problem statement
The #1 status call a shop fields is "is it ready yet?" Today the answer is a status field and a track page with no time estimate, and jobs silently stall in a status with nobody noticing until the customer complains. Every status transition is already timestamped in RepairOS — the data to predict a ready-time and flag stalls exists and is unused. Orderry models neither, so this is pure differentiation from data we already have (no schema change, no tech behavior change).

### Goals
- Give the customer track page a real predicted ready-time learned from the shop's own cycle times.
- Surface stalled jobs to the owner/manager before they become an angry call.
- Ship the free-data win first: it de-risks Feature 1 by proving the transition event stream is trustworthy.

### Non-goals
- No promises/SLAs to the customer — the predicted time is an estimate, labelled as such. Out of scope: contractual ready-time guarantees.
- No new scheduling engine. This reads historical dwell times; it does not reassign work or optimize the queue.
- No per-tech performance scoring in v1 (politically loaded; can come later as owner-only). v1 flags the *job*, not the person.
- No schema change in v1 — computed from existing status-transition timestamps.

### User stories
- As a customer, I want to see when my device is expected to be ready so that I don't have to call the shop.
- As a shop manager, I want jobs that are taking longer than our typical time for that status + device type to be flagged so that I can intervene before the customer is upset.
- As a shop owner, I want the prediction to learn from my shop's actual history so that the estimate reflects reality, not a generic default.
- As a technician, I want the "typical vs this one" band on the status bar so that I can see at a glance if a job is running long.

### Requirements

**P0 — Must have**
- P0-1 · Dwell-time model: a query/service computes rolling average (and p50/p80) dwell time per (status, order_type or device_type) from existing status-transition history, per org/location. Cold-start fallback: if a bucket has < N samples (e.g. N=5), fall back to a coarser bucket (all device types) or hide the estimate. Acceptance: given historical orders, the service returns a defensible average per status; given a bucket under N, it returns "not enough data" rather than a wild number.
- P0-2 · Stall flag: an order whose time in its current status exceeds p80 (or avg × configurable factor) for its bucket is flagged `stalled`. Surfaced on the WO status bar and in the WO list as a badge/filter. Acceptance: an order sitting 90 min in a status whose p80 is 47 min shows the stalled badge; one at 30 min does not.
- P0-3 · Predicted ready-time: sum of remaining-status average dwell times from current status → a terminal ready status, rendered on the WO and the customer track page, labelled as an estimate. Acceptance: track page shows "Estimated ready: today ~4:35 PM" and updates as the order advances.
- P0-4 · Status-bar overlay: the existing status bar/transition graph gains a "typical: X / this one: Y" band on the active status. No layout rewrite — overlay only. Acceptance: renders on the current WO status without breaking the existing graph.

**P1 — Nice to have**
- P1-1 · Owner dashboard tile: count of stalled jobs, oldest stall, ranked by overdue.
- P1-2 · Notification/automation hook: reuse the existing AutomationRule engine so a stall can trigger a notification. (Config only — the engine exists.)
- P1-3 · Confidence indicator on the prediction (sample size / variance) so a thin bucket reads as rough.

**P2 — Future**
- P2-1 · Per-tech and per-location cycle-time comparison (owner-only).
- P2-2 · Business-hours-aware prediction (don't count overnight/closed hours toward "typical").
- P2-3 · Feed predicted-ready into booking capacity.

### Success metrics
- Leading (validation, pre-ship): run the model on historical orders and check predicted-ready would have landed within ±X% for ≥Y% of completed jobs. If it can't clear this bar on real data, don't ship P0-3 (ship stall detection alone). — see Open Questions.
- Leading (30 days): % of stalled jobs where a manager acts (status change / note) within Z minutes of the flag.
- Lagging (quarter): reduction in inbound "is it ready?" contacts; reduction in jobs sitting > p80 in a status.

---

## Cross-cutting

### Data & architecture notes
- Both features read/write existing models: `checklist_template_items`, `order_checklist_items`, `Recommendation`, `Invoice`/`InvoiceLine`, `OrderStatus` + transition history. Feature 1 adds nullable columns (single migration); Feature 2 adds none in v1.
- Feature 2's dwell-time computation should be a read-model/service (cache the per-bucket averages; recompute on a schedule or on transition write) to avoid recomputing on every WO open. `ponytail:` start with an on-read query + short cache; move to a materialized table only if WO-open latency measurably suffers.
- All new endpoints honor existing org-scoping and RBAC; no new auth path.

### Open questions
- [data] What's the minimum sample size N per bucket before a prediction is trustworthy? Resolve by running P0-1 on the real DB (this is the validation step). **Blocking for Feature 2 P0-3, not for stall detection.**
- [data] Does predicted-ready need business-hours awareness in v1, or is wall-clock good enough for a first cut? (Likely P2, confirm on real data.)
- [product] For Feature 1, is threshold config per-template-item enough, or do shops need per-product default thresholds? (Lean: per-item, keep it simple.)
- [product] Dismiss behavior — should a dismissed draft stay dismissed forever for that order, or re-offer if the value changes again? (Lean: stays dismissed for that saved value; re-offers only if the value crosses again after an edit.)
- [eng] Recompute cadence for the dwell-time cache — on every transition write, or a periodic job? Depends on transition volume per org.

### Timeline / phasing
1. **Phase A — Feature 2 validation (no UI):** build P0-1 dwell-time service, run on real historical orders, answer the sample-size open question. Cheapest, proves the event stream. Gate: predictions clear the accuracy bar OR we ship stall-detection-only.
2. **Phase B — Feature 2 ship:** stall flag + status-bar overlay + track-page predicted-ready (whichever P0s cleared Phase A).
3. **Phase C — Feature 1:** template config → trigger → WO draft surface → reuse recs/approval. Ships after B because B validates the transition/event plumbing Feature 1's triggers rely on.

Each phase follows the standard loop: TDD backend, `tsc`/vite build, gates, changelog entry, live verify.
