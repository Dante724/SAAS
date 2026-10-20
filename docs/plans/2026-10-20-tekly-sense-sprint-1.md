# Tekly Sense — Sprint 1: "It knows my shop" (20 Oct 2026)

Council: PM (scope, wording), engineer (build), tester (failing tests first, then a
review of the tests), security (review of the diff). Same TDD rule as sprint 0.

No GPU, no AI service, no new dependency: everything is SQL plus a little Python over
the shop's own history.

## Scope
1. **Foundation**
   - Settings stored per shop in `automation_settings` (key `sense`), so no migration.
     Master switch plus per-feature switches: job suggestions, deferred work, ready-time
     "why", margin check. All on by default; they only ever suggest.
   - `GET /api/sense/settings` (anyone signed in: the app needs to know what to show) and
     `PUT` (shop owner only, audited `sense.settings.update`).
   - Learning state: a shop needs 20 closed jobs before suggestions appear. The settings
     reply carries the count, for the progress bar.
   - Permission `can_use_sense_suggestions`: opt-in (legacy roles do not get it), on in
     the Full access and Manager templates only. Margin needs the existing
     `can_see_profit` + `can_view_purchase_price`.
2. **Job suggestions** — `POST /api/sense/draft {complaint, device_id?}`: similar closed
   jobs from the last 2 years (word overlap, same brand/model counts extra); lines used
   on at least 30% of them (and at least 2 jobs) come back with the usual price and a
   "why" (n of m similar jobs). Prices only for roles that can see prices. Counts only,
   never other jobs' numbers or customers.
3. **Deferred last time** — `GET /api/sense/orders/{id}/deferred`: work this customer
   said "not now" to on earlier jobs for the same item (or the same customer when the job
   has no item). Respects "own orders only".
4. **Margin check** — `GET /api/sense/orders/{id}/margin`: this job's margin vs the shop's
   usual margin over the last year (needs 20 closed jobs with lines). Flags when 15+
   points under. Owner, or roles with see-profit and purchase-price, only.
5. **Ready-time "why"** — data already exists (`/orders/{id}/timing`); the UI explains it.
6. **UI**
   - Settings → General: "✦ Tekly Sense" section, owner only.
   - New-job drawer: "✦ Suggested" chips with "Why?" replace the purple AI button. The AI
     draft stays available as a quiet text link.
   - Work order: deferred items above Recommendations ("Add as draft"), margin note above
     the totals, "Why?" on the ready-time band.

## Tests (written first)
Defaults and owner-only save with audit; learning state; suggestions found from history;
tenant isolation; permission off for legacy and template roles; prices hidden without the
price permission; feature switch off; deferred list and cross-shop 404; margin owner-only
and the flag.

## Review outcomes (folded in before commit)
Security review: approve with changes.
- Deferred work now hides earlier jobs the caller can't open (own-jobs-only roles, other
  branches, hidden statuses).
- Job suggestions are shop-level knowledge on purpose, like the catalog: recurring line
  names, a median price (stripped without the price permission) and counts only.
- `usual_pct` joined the cost-redaction keys; the margin query takes the latest 2,000 jobs
  and treats a missing cost as 0.
- `/me` now says `is_shop_owner`, the same check the server uses for the settings.
- Only the owner gets the learning count from `GET /api/sense/settings`.
- Settings body is a fixed shape (the four features).

Tester review: 6 accuracy bugs fixed, each with a test in `test_sense_sprint1_edge.py`.
- Complaints in any script match (Cyrillic, accents), and model numbers like "13" count.
- Similarity is the share of the shorter complaint's words found in the other (two shared
  words needed), so long, chatty complaints still match.
- A catalog line and a typed line with the same name merge; a line repeated on one job
  counts once, at that job's price.
- Margin uses revenue before tax and after discounts.
- Deferred work shows once, and drops off once a later visit did it.
