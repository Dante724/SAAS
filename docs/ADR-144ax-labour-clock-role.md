# ADR 144ax — Role of the per-line job clock

**Status:** Superseded by Option B · 2026-10-01 (audit log shipped in 144ay — see note at end)
**Context builds:** 144au (per-line clock-in), 144av (attribution + booked-vs-actual), 144aw (hardening), 144ax (this), 144ay (Option B audit log).

## Context

The work-order job clock now records *whose* time it is, per service line, and feeds both the
commission engine and — once a timesheet entry is approved — an hourly-pay posting. The senior
architecture review (144aw/ax) flagged that the system was quietly behaving as a **payroll system
of record** (approval posts pay) without the guarantees that role demands (append-only edit
history, immutable rate pinning, hard approval gates). That ambiguity is the risk, not the code.

## Decision

**The per-line job clock is an EFFICIENCY & COMMISSION instrument, not the compliance system of
record for payable hours.**

- Its outputs — per-line actual hours, booked-vs-actual variance, the Labour-efficiency report,
  and commission attribution — are for coaching, costing, and commission, which tolerate later
  correction.
- Payable **attendance** hours remain the domain of the attendance clock / timesheet approval
  flow, which is where payroll truth lives.
- The existing "approve → post hourly" path stays, but is treated as a *convenience estimate of
  labour cost*, not an auditable paycheck source.

## Consequences

- **No append-only edit-audit table is required now** (YAGNI) — chosen deliberately because the
  clock is not the pay source of record. `TimeEntry` keeps `updated_at`, `state`
  (running/stopped/approved/void), and `approved_by_id`, which is sufficient for an efficiency
  clock.
- Rate pinning on `TimeEntry` (`cost_rate`/`billable_rate` snapshot at start) is retained — it
  makes Option B (full system-of-record) reachable later without a data migration.
- **Revisit trigger:** if a customer decides the job clock should *be* the payable-hours source,
  this ADR must be superseded by one that adds (a) an append-only `TimeEntryAudit` log, (b) a hard
  approval gate before any downstream use, and (c) a tech-facing review/dispute surface. That is a
  dedicated epic, not a flag flip.

## Alternatives considered

- **Option B — clock as payroll system of record now.** Rejected for this cycle: it requires the
  audit log + dispute flow above, which is a separate epic and wasn't requested. Keeping the clock
  as an efficiency instrument delivers the labour-attribution value without the compliance surface
  area.

## Update — 144ay: Option B adopted

The audit-log groundwork was requested and built. The job clock now has an **append-only
`TimeEntryAudit` trail** (`time_entry_audits`): every manual create, field edit, approve, reopen,
and void writes an immutable row of action + field + old→new + actor + timestamp, readable at
`GET /api/timeclock/entries/{id}/audit`. Rows are never updated or deleted. Rate pinning on
`TimeEntry` already made this reachable without a data migration. With the edit history in place,
the clock is now defensible as the **system of record for payable hours** (Option B). Still
outstanding before fully relying on it for pay disputes: a tech-facing review/dispute surface
(the audit trail is currently manager/owner-readable + the entry's own owner).
