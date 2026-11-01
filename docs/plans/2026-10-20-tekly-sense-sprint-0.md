# Tekly Sense — Sprint 0: security base (20 Oct 2026)

Goal: close the gaps the Tekly Sense council found in today's code before any Sense
feature builds on them. Each fix starts with a test that fails on the old code.

Council roles: PM (scope + order), engineer (fix), tester (failing test first, full
suites after), security (review the diff before commit).

| # | Finding (verified in code) | Fix | Test |
|---|---|---|---|
| 1 | Public lookup throttles by order number across *all* shops (`num:{n}`), in-process only, so flooding "#1001" locks every shop's customers out and the limit multiplies per worker | Shared limiter (`ratelimit.aenforce`, Redis when configured); order-number bucket keyed by shop; 5 tries per order per hour | Flood shop A's order → shop B's customer still gets in; 6th try on A is 429 |
| 2 | `/appointment/{slug}/hello` turns a phone number into the customer's first name | Return only `known`; shared limiter per shop + IP | Known phone → `known: true`, no name |
| 3 | `/track/{token}/message` has no rate limit (spam into a shop's inbox) | 10 messages per token per 10 min, shared limiter | 11th message → 429 |
| 4 | Insights `metric=gross_profit` shows profit to anyone with "view reports" | Require `can_see_profit` for the profit metric | Role with reports but no profit → 403; owner → 200 |
| 5 | `run_due` sends scheduled messages even if the customer opted out after they were queued | Re-check the client's consent at send time | Opt out after scheduling → skipped, not sent |
| 6 | Appointment notifications skip the consent check | Same `channel_consented` gate as order notifications | Opted-out client → skipped |

Design note (security review): the lookup's order-number bucket is per caller
(`{shop|*}:{ip}:{order}`, 5/hour) on purpose. There is no bucket shared by everyone,
because order numbers repeat between shops and any shared bucket lets one person lock
every shop's customers out. Guessing the phone that pairs with an order is a 10-digit
search, so a per-caller limit plus 20 lookups/min per IP is the right trade.

Deferred to the sprint that needs them (noted, not forgotten):
- New Sense permissions must be added to `OPT_IN` so legacy roles don't get them (Sprint 1, when the codes are created).
- Email unsubscribe link + endpoint, explicit marketing consent (Sprint 4, agents).

Done when: new tests fail on the old code and pass on the new; full backend suite,
frontend tsc/lint/tests/build pass; security reviewer signs off on the diff.
