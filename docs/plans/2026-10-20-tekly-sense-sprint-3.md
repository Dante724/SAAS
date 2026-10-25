# Tekly Sense — Sprint 3: "It watches my money" (20 Oct 2026)

Same council (PM, engineer, tester, security), TDD, browser check. No AI service, no GPU,
no new dependency.

## Who did it (new, needed for any fair flag)
Today a discount on a job line, a refund and a drawer close don't record who did them. One
migration adds `order_lines.discount_by_id`, `refunds.created_by_id` and
`cash_shifts.closed_by_id`. A single before-flush hook stamps them from the signed-in person,
so every code path that discounts, refunds or closes a drawer is covered without editing
each one. The person comes from `get_current_employee`, which puts their id on the request's
database session.

## Watchdog (owner only)
Last 30 days, per person, in this shop only:

| Flag | Flagged when |
|---|---|
| Big discounts | Discounts at or above the owner's threshold (default 20%): at least 3, and at least twice what others usually give |
| Refunds | At least 3, and at least twice what others usually do |
| Voids | Sales and refunds voided: at least 3, and at least twice what others usually do |
| Cash short | A drawer closed more than the owner's tolerance (default 10) under expected |

Fairness rules:
- Each flag shows the rule, the person's number, what others usually do (median), and up
  to 5 records as evidence.
- The wording is "unusual", never "suspicious". Nothing happens automatically, and nothing
  goes into payroll.
- The owner can mark a flag as reviewed (audited). It stays hidden for that week, or for that
  drawer.
- Only the shop owner can open it: not managers, and not any role. Nobody else sees
  anything about it.

## Team scorecard (owner only)
Last 30 days per active person:
- jobs closed and their value;
- comebacks (the same item back within 30 days);
- big discounts and refunds.

## Morning Brief
The owner's brief adds "N unusual things in Watchdog". Other readers never see it.

## Settings
Settings → General → Tekly Sense gets a "Watchdog and team scorecard" switch, plus the
discount threshold and the cash tolerance (owner only, audited).

## Tests first
- Attribution is stamped on create and change.
- Each flag appears, and only once the minimums are met.
- Evidence and median are shown.
- Owner only: a full-permission manager gets 403.
- Tenant isolation.
- Reviewing a flag hides it and is audited.
- Scorecard numbers.
- The owner's brief has the Watchdog line; other readers don't.
- The switch turns it off.

## Review outcomes (folded in before commit)
Security review: approve with changes.
- Duplicating a job keeps each copied discount with whoever gave it, not whoever duplicated it.
- A short drawer with no recorded closer names nobody ("wasn't recorded"). It is never pinned
  on whoever opened the drawer.
- Staff can't see whether Watchdog is on or where its thresholds are (so nobody can stay
  just under them), nor who gets the brief.
- Review keys are validated.
- Every limited query keeps the newest rows.
- Line discounts count when the job was created or changed in the window.
- The comeback lookup is bounded in time and skips estimates and archived jobs.

Tester review: needs one more pass. 22 edge tests are now in `test_sense_sprint3_edge.py`.
- A job is never its own comeback.
- A whole-basket discount on a counter sale counts.
- A reviewed flag is keyed on its latest event, so it stays hidden until something new happens
  (instead of coming back every week for the same events).

Browser check (demo data):
- Three flags with "Why?" evidence; Mark reviewed hides the flag and is audited.
- The scorecard table, the brief's Watchdog line, and the threshold saved from Settings all
  behaved as designed.
