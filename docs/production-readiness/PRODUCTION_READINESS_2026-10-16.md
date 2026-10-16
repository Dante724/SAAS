# RepairOS: go/no-go, 16 Oct 2026

Branch `security/phase-4`: 104+ commits ahead of `main`, none of it pushed. Everything here
was checked locally for free; no paid CI was used.

## Verdict: not yet ready, but close

The two critical holes from the 14 Oct audit are fixed, all tests pass, and no dependency
has a known vulnerability. What's left is a short list of real gaps (below), plus pushing,
merging and running CI once on the exact code you ship.

## What was run

| Check | Result |
|---|---|
| Backend suite, SQLite (all tests) | ✅ 1,489 passed, 0 failed |
| Backend suite, Postgres 16 (all tests, serial like CI) | ✅ 1,489 passed, 0 failed (32 min) |
| Migrations from empty on Postgres 16 | ✅ clean, single head |
| Frontend typecheck, UI-text lint, 141 tests, build | ✅ all pass |
| `npm audit --omit=dev` | ✅ 0 vulnerabilities (the `xlsx` and `react-router` issues are gone) |
| `pip-audit` on `requirements.lock` | ✅ no known vulnerabilities |
| `ruff` on the whole backend | ✅ no real errors (no undefined names); 213 style-only warnings |
| Browser check of every page (light and dark) | ✅ |

## The 14 Oct audit, item by item (checked against current code)

| Group | Fixed | Partly fixed | Open |
|---|---|---|---|
| Critical (C1, C2) | 2 | 0 | 0 |
| High (H1–H7) | 5 | 2 (H3, H5) | 0 |
| Medium (M1–M11) | 5 | 6 | 0 |
| Money (B1–B23) | 13 | 10 | 0 |
| Platform (P1–P9) | 3 | 6 | 0 |
| Performance (F1–F6) | 1 | 4 + 1 unclear | 0 |

## Update, later on 16 Oct: launch fixes batches 1 and 2

Fixed with regression tests (`backend/tests/test_launch_batch1.py`, `test_launch_batch2.py`):

| Item | What changed |
|---|---|
| B12 coupon → cash | A coupon is capped at what's still owed; a paid order refuses it. "Unlimited" (0) coupons now work at the POS. |
| B3 pay links | Links the customer already opened are cancelled too, and the Stripe checkout is expired at Stripe. **Square links are not closed yet** (only Square's order id is stored); a late payment is still recorded, as an overpayment. |
| M3 own-jobs limit | Bulk assign / priority / invoice / group invoice skip jobs the person can't see; bulk archive/restore and the job-items routes check it too. |
| M9 custom reports | Locked to "view reports" (Phase 1). |
| B10 till close | Cash taken against an invoice joins the open drawer shift; refunds from archiving come out of the drawer count. A shift id from another shop is refused. |
| B1/B11 refund races | Archiving and overpayment refunds lock the order row. |
| B21 over-receiving | A PO line can't receive more than is still outstanding (409 with the numbers). |
| H5 template HTML | Cleaned on the server with nh3 when saved and again at every render (old templates included). Placeholders and `{#if}` blocks survive; the built-in templates are unchanged. |
| P9 GDPR | Erase now destroys intake photos/markups, e-signature images and signer details, photos/files on the subject's jobs and feed, recommendation photos and asset pictures (bytes deleted, shared files kept). Export adds calls and inquiries. PDFs are never stored, so there is nothing to erase there. |
| M4/M5 webhooks | Unsigned provider webhooks are accepted only when ENVIRONMENT is explicitly development/test; a server that never set it now fails closed. |
| Test messages | "Send me a sample" needs settings rights (it fills a real customer's job into the message). |

Still open from the list below: H3 (permission codes per route, a product decision per code), P6 (make the Postgres CI job required), P3 backups automation, P2/P4 notification outbox, F1/F2/F4 list limits, H1 forced reset for old default passwords, and Square pay-link expiry.

## Must fix before real customers (no-go items)

1. **Coupon can turn into cash (B12).** A coupon on an already-paid order is capped at the
   order total, not what's owed, so the order becomes "overpaid" and the overpayment can be
   paid out from the till. New bug from the same fix: unlimited coupons are rejected at the POS.
2. **Pay links stay live after the order is settled (B3).** A link the customer already
   opened isn't cancelled, and the Stripe/Square checkout isn't expired, so they can pay twice.
3. **"Own records only" isn't enforced on bulk actions (M3).** A technician limited to their
   own jobs can bulk-assign, archive or invoice other people's jobs.
4. **Custom reports are open to every role by default (M9),** including costs and customer
   phone/email.
5. **132 write routes need only a login (H3).** They are listed in
   `backend/tests/ungated_write_routes.txt`; 37 permission codes in the catalogue are never
   checked, so turning them off in a role does nothing.
6. **Template HTML isn't cleaned on the server (H5).** Only a hand-written client-side
   sanitizer stands between a template and a stored-XSS.
7. **Till close still misses some cash (B10)** (invoice payments without a shift, archive
   refunds), and **purchase orders can be over-received (B21).**
8. **Postgres must be a required CI check (P6).** It is still `continue-on-error`.

## Should fix soon after

- Backups: the runbook exists (`DEPLOY.md`) but there is no automation and no drill on record (P3).
- GDPR erase misses photos, signatures and generated PDFs; export misses calls and leads (P9).
- SMS/email still send inline after commit with no outbox (P4); rows stuck in "sending" after a crash are never retried (P2).
- `ENVIRONMENT` defaults to `development`; a deploy that forgets it accepts unsigned webhooks (M4/M5). The Dockerfile sets it.
- Refund/archive races: archive and overpayment refunds don't lock the order row (B1, B11).
- Lists without limits (product list, client/order lists up to 1,000) and ~37 tables without an `org_id` index (F1, F2, F4).
- Existing staff accounts with the old default password aren't force-reset; run `scripts/find_default_passwords.py` (H1).

## Before you ship (outside the code)

- Make `github.com/Dante724/SAAS` private if it still is public. If any old build was ever
  reachable from the internet, rotate `SECRET_KEY` and stored integration keys.
- Push `security/phase-4`, merge the Gmail/Outlook email branch and the tax/POS branch, then
  let CI run once on the result.
