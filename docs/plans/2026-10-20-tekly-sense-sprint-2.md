# Tekly Sense — Sprint 2: "It briefs me" (20 Oct 2026)

Same council (PM, engineer, tester, security), TDD, then a browser check. Still no AI service,
no GPU and no new dependency.

## 1. Morning Brief
- **What it says** (computed on request, from this shop only):
  - Yesterday: jobs closed, money taken (finance roles only).
  - Today: jobs due.
  - Worth a look: jobs stuck in one status for 5+ days, quotes waiting 2+ days, overdue
    invoices (amount only for finance roles), customers due for service in the next 14 days.
- **Who sees it**: the shop owner plus the people the owner picks. Anyone else gets 403.
  Money lines follow each reader's own permissions (`can_view_finance`).
- **Where**:
  - A card at the top of the Today page.
  - Email and push, both off until the owner turns them on, sent once a day after the
    chosen hour in the shop's time zone. The day is claimed before sending, so a restart
    never sends twice. Push text is generic ("Your morning brief is ready"): nothing
    private on a lock screen.
- **Settings**: Settings → General → Tekly Sense: on/off, hour, email, push, recipients
  (people in this shop only, validated server-side; owner only, audited).

## 2. Usually added together
`GET /api/sense/orders/{id}/together` — for lines already on the job, other lines that appear
on 40%+ of the shop's past jobs with them (at least 3 jobs, line seen on 5+ jobs). Usual
price and "why". One tap adds the line. Needs `can_use_sense_suggestions`; prices follow the
price permission.

## 3. Service due
`GET /api/sense/orders/{id}/service-due` — for the job's item: services this shop repeats on
the same item at a regular interval (median gap, 5+ repeats, 14 days to 3 years), and when
this item last had them. Shown when due within 14 days (or overdue). One tap adds it as a
draft recommendation. Also counted in the Brief.

## Tests first
Brief: contents and counts, money hidden without finance, non-recipient 403, recipients
must be in the shop, sent once after the hour in the shop's time zone, never before, never
twice, generic push, nothing when off. Together: found from history, never across shops,
learning gate, permission. Service due: interval learned, due shown with last-done date, not
when not due, not when already on the job.

## Review outcomes (folded in before commit)
Security review: approve with changes.
- A failed send for one reader no longer stops the others: the session is rolled back, and
  readers are read up front, so the rollback can't expire them. Found again in testing:
  the first fix alone still broke the next reader; the test now forces a real failed
  statement on whoever is first.
- Service history is cached per shop for 10 minutes (keyed by shop), and the brief is
  built once per money level rather than once per reader.
- A recipient who left is dropped quietly on the next save instead of locking the owner out
  (a 422 now only means "not in this shop").
- Payments count only when every link they have is this shop's.
- Settings saves and the daily send lock the settings row, so neither overwrites the other.
- Service-due drafts carry no price when the person can't see prices.
- The UI says counts cover the whole shop.

Tester review: ship with fixes. 15 edge tests are now in `test_sense_sprint2_edge.py`
(daylight-saving days, local midnight, bad time zone, people who left, refunds, partial,
cancelled and draft invoices, estimates not counted as stuck, together matching, interval
bounds, overdue window).
- "Usually added together" matches past lines with extra spaces or non-ASCII case.
- Service due uses the shop's local day.
- The brief's service-due count skips items already in the shop on an open job.

Browser check:
- The Today card, the brief settings (email, a recipient) and the work-order chips (added
  Tempered glass, Oil change as a draft) all worked against demo data.
- The brief email preview was right, apart from amounts without a currency symbol, which
  now use the shop's symbol.
