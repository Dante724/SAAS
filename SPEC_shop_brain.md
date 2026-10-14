# Shop Brain — Product & Architecture Spec

*Status: design / pre-build. Produced from deep research → brainstorm (×2 PM methods) → council review.*
*Owner concern honored throughout: the agent is **per-shop isolated**, never one shared brain across shops.*

---

## 1. The bet in one line

**Shop Brain is a per-shop autonomous operating intelligence that finds the money a repair shop is leaking and acts on it — approval-first — surfacing everything as one money-ranked queue run by an owner-facing chief-of-staff.**

Not a chatbot. Not a dashboard. Not a declined-work texter (that's the *basic* version). The ambition is a system that turns the shop's own inert event stream into prediction + judgment + action, and that *becomes the owner's brain* the longer it runs.

---

## 2. Why now — the problem, with evidence

A repair shop is a job shop that leaks money at every **handoff**, and research confirms the structural truth: *no single person has the full picture* of job status, parts, and approvals.

| Leak | Evidence |
|------|----------|
| Declined / deferred work never chased | Shops lose **$750K–$2.2M/yr**; **60–70%** of declined work goes to a competitor within 12 months |
| …but it's recoverable | Automated follow-up books **18–28%** of deferred repairs within 45 days (texts at day 14 + 30) |
| No-shows | **15–20%** rate ≈ **$31K/yr** lost for a typical shop |
| Scheduling | **45%** of shops call inefficient scheduling their #1 bottleneck |
| Parts delays | **60%** cite parts delays as a major daily disruption |
| Comebacks | **≈half** of repair returns are *communication* failures (not tech error); each re-occupies a bay + unpaid labor |

**Competitive white space:** ServiceTitan's *Atlas* is a copilot that *helps the service advisor*; Tekmetric/Shopmonkey ship a *Declined Jobs Report*. Per industry analysis, **none run a full autonomous agent** that chases declined work, qualifies leads, chases invoices, and reports — with the conditional logic a busy shop needs. The analogy that de-risks the wedge: dental "treatment coordination" agents unlocked **up to 50%** of revenue trapped in unapproved plans — structurally identical to declined repair work.

---

## 3. The product (the experience)

**One surface: a money-ranked action queue, run by a chief-of-staff ("Owner Brain").**

- The owner opens Shop Brain and sees **"today's money"**: a short, ranked list of actions, each with the **$ at stake**, a **one-tap** execute, and a **draft already written in the owner's voice**.
- Nothing is six dashboards. Every detector (below) feeds *one* queue. Ruthless prioritization — only surface what clears a $ threshold, so the queue never becomes another ignored inbox.
- A daily/weekly **chief-of-staff digest**: "3 things that matter, $X in play, here's what I'd do."
- Everything customer-facing is **approval-first**: the Brain drafts, the human taps send. That is the trust model, the liability firewall, and the training-data pipeline all at once.

**Design guardrails from council:** no setup weekend (works off data that already exists); sounds like the owner, not a corporate chain; conservative about who it contacts and how often; respects consent + quiet-hours.

---

## 4. The engine (the detector constellation)

Each "brain" is a **detector** feeding the one queue — not a separate app.

1. **Recovery Brain** *(the wedge, Phase 1)* — ranks declined/deferred work and lapsed customers by **expected value**, drafts the follow-up in the owner's voice at the learned-best time. Metric: recovered $/mo.
2. **Comeback Brain** *(pure margin protection)* — detects comeback risk (vague complaint, skipped checklist item, **no intake photos**, rushed turnaround) and forces the gap closed *before* the device leaves. **Its sensors already exist** — see §6.
3. **Promise Brain** — predicts no-shows and true ready-time (bay load + parts ETA); keeps the customer promise honest.
4. **Parts Brain** — watches open WOs, predicts stockouts, drafts POs before a bay is blocked.
5. **Reputation Brain** — times the review ask to the happiest moment; flags churn risk early.
6. **Owner Brain (chief-of-staff / orchestrator)** — merges all detectors into the ranked queue + digest; learns the owner's preferences; routes approvals; owns the autonomy dials.

---

## 5. Architecture & non-negotiables

- **Per-shop isolation (inviolable).** Every query is already `org_id`-scoped; the Brain gets **no cross-org read path, ever**. One shop can never see or infer another shop's data.
- **Shared intelligence = aggregate priors, not a data lake.** Cross-vertical learning is *statistics* ("shops like yours convert marine winterization 3× at day 10"), computed so no raw record of one shop is reachable from another. Treat the priors like a *published model*, not customer data.
- **Tool-constrained actions.** The agent proposes **structured actions from a fixed catalog** (draft message, create follow-up, draft PO, request review…), never free-form effects. A hallucinated *draft* is harmless; a hallucinated *refund* is a lawsuit. Money-moving and customer-facing actions are always gated.
- **Approval flywheel → graduated autonomy.** Every approve / edit / reject is a labeled example. After it has learned the owner's judgment, expose **trust dials** that promote specific action types from *draft-for-approval* → *auto* ("auto-send review asks; always ask me before quoting > $500"). The product grows **dials, not features**: copilot → autopilot, per action type, earned.
- **Memory.** Per-shop working + episodic + semantic memory (what was tried, what converted, the owner's voice & pricing). Heuristics first; learned models only where the queue has generated labels.
- **Consent & quiet-hours** enforced on every send (reuse existing notification rules + consent flags).

---

## 6. It's already half-built (the sensors exist)

The recent work wasn't incidental — it wired the Brain's nervous system:

- **Intake photos + watch-outs + checklists** → Comeback Brain's risk sensors.
- **Declined/recommendation model** (status + `decided_at`) → Recovery Brain's input.
- **Contact ledger, balances, merge/undo** → clean customer records to act on.
- **Notification engine + rules + consent + seeded defaults (144ah)** → the Brain's safe send channel.
- **Deposit, payment links, saved filters, scan-to-open** → one-tap action targets.

Shop Brain is largely an **orchestration + ranking + drafting layer** over systems that already exist — which is exactly why it's buildable.

---

## 7. Moats

1. **Multi-vertical first-party priors** (auto/marine/phone/bike/small-engine) a single-vertical competitor *structurally cannot* have.
2. **The approval flywheel** — the owner-voice + judgment model compounds per shop; switching cost grows with tenure.
3. **Outcome-based pricing** (below) — risk-reversal a seat-priced competitor can't match.

---

## 8. Business model (council's "what would Stripe/Ramp do")

Price on **recovered-revenue share**, not a seat: *"Shop Brain made you $4,200 this month; you pay $X."* Aligns incentives, removes adoption fear, and is itself a moat — **only credible if attribution is credible** (see §9).

---

## 9. The one metric + honest attribution

**Recovered + protected revenue per month**, measured against a **holdout** (a control slice that the Brain does *not* act on) so the number is a believable counterfactual, not a vanity stat. If we can't prove it counterfactually, it's a vitamin — the investor won't fund it and the owner won't trust it.

---

## 10. Risks & the first test to run (before building anything)

- **Riskiest assumption: will owners *act* on the queue?** If it becomes another ignored inbox, the whole thing dies.
- **Cheapest test — shadow mode, no build:** run the Recovery detector over a shop's *existing history* and show *"here's $X you left on the table last quarter, and who."* If that doesn't make owners lean in, nothing will. This is the go/no-go gate for the entire program.
- Other risks: prediction trust (→ start heuristic, earn ML with labels); privacy/isolation (→ §5 non-negotiables); over-contacting customers (→ conservative + consent); hallucinated actions (→ fixed tool catalog + gating).

---

## 11. Phased plan

**Phase 0 — Shadow / proof (days, mostly analysis).** Run Recovery ranking over existing declined + lapsed data per shop. Output a read-only "money left on the table" report + holdout design. *Gate: owners lean in.*

**Phase 1 — Recovery → queue → one-tap (the product's spine).** The money-ranked queue, owner-voice drafts, approval-first send over the existing notification channel, recovered-revenue measured vs. holdout. Per-shop isolated; tool-constrained; labeling pipeline on from day one. *Gate: measurable recovered $.*

**Phase 2 — more detectors into the same queue.** Comeback (reusing intake photos/watch-outs/checklists), then Promise, Parts, Reputation — each adds ranked actions, no new surface. *Gate: each detector earns its place by $ or bays saved.*

**Phase 3 — graduated autonomy.** Trust dials per action type; chief-of-staff digest; aggregate priors bootstrap new shops. *Gate: owners promote actions to auto without regret.*

Each phase ships under the same discipline used all session: TDD on logic, per-shop isolation verified, approval-first, gates (tsc/build/tests/live-verify) + CHANGELOG.

---

## 12. Open questions for the owner (to settle before Phase 1)

1. **First detector confirm:** Recovery first (highest measurable $) — agreed, or lead elsewhere?
2. **Pricing appetite:** outcome-share vs. flat add-on — which fits the business?
3. **Autonomy ceiling:** how far should the dials ever go (which actions may *never* be auto)?
4. **Shadow-mode data:** run the Phase 0 proof on the current dev/seed data, or wait for a real shop's history?
