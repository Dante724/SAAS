# Launch readiness audit — 19 Oct 2026

Branch audited: `website/landing-page` @ `0fec37c` (contains `security/phase-4` through Batch D).
Builds on `docs/LAUNCH_QA_2026-10-19.md` (UX/flow pass earlier today); this pass covers security,
auth, tenant isolation, payments, data integrity, deployment, website and legal.

Nothing in the app was changed. Every finding below marked **verified** was reproduced with a test
or confirmed in the code; scratch tests and throwaway databases (SQLite + a temporary Postgres 16)
lived outside the repo.

**Stack:** FastAPI + SQLAlchemy async (Python 3.11 in Docker), Alembic, Postgres in production
(SQLite in dev/test), React 18 + Vite + Tailwind (`frontend-react`), a legacy vanilla-JS app
(`frontend/`, served at `/tech`, `/pay`, `/admin`), static marketing site (`website/`). Stripe,
Square, SumUp, Twilio, Meta, SMTP, Anthropic, QuickBooks/Xero, S3 integrations.

---

## Fix status (same day, uncommitted on `website/landing-page`)

Fix-plan steps 1–9, 11 and 12 are done and tested. Step 10: paid plans stay as advertised; Stripe Billing (trial expiry, plan and seat limits) is to be built once the company is registered. New
tests: `backend/tests/test_launch_blockers.py` (34), additions to `test_deployed_hardening.py`,
`frontend-react/src/components/Shell.test.tsx`. Backend suite 1671 passed before the last three
test fixes (those 14 now pass); vitest 175/175; `tsc`, build, UI-text lint, ruff clean; migrations
`ls144r1`–`ls144r4` verified on SQLite and Postgres 16 (single head `ls144r4`).

| Item | Status | What changed |
|---|---|---|
| C1 order lines | Fixed | API derives `line_type` from `kind`; drawer sends it plus catalog `ref_id`; bundles go through add-line. |
| C2 duplicate statuses | Fixed | `uq_order_statuses_org_rt_key` + dedupe migration (repoints transitions/feedback); insert-or-ignore seeding; group lookups use `.first()`. |
| C3 PO numbers | Fixed | `purchase_orders.org_id` (backfilled), `UNIQUE(org_id, po_number)`. |
| C4 XSS | Fixed | `/tech`, dashboard, toasts, pay and admin pages escape everything; `script-src 'self'` everywhere but `/admin`; SW cache bumped to v7. |
| C5/C6/C8 config | Fixed | Deploy refuses SQLite, non-https `PUBLIC_BASE_URL`, missing SMTP, `repairos.dev`; return/callback URLs derive from `PUBLIC_BASE_URL`; simulated email fails (no body logged) when deployed. |
| C7 pay links | Fixed | Stale amount → reissue; revoke closes at Stripe/Square; pay on a revoked link is recorded and flagged; checkout capped at balance; no simulated gateway when deployed; network errors → 502. |
| C9 website links | Fixed (check domain) | `appUrl` = `https://app.tekly.app` — confirm this is the real app address. `demoEndpoint` still empty (mailto). |
| C10 legal | Drafted | `/privacy/`, `/terms/` with footer links, demo-form notice, signup consent (audited). **Fill the `[[…]]` placeholders** (company, address, hosting, email provider, retention days, refund rule, jurisdiction) and have them reviewed. |
| C11 mobile | Fixed | Drawer navigation below 1024 px; verified at 375, 768, 1280 px. Shop name replaces "Workshop Central" (sidebar, stickers). |
| C12 rate limits | Fixed | Only `/api/` counted, per user when signed in; limits on register/reset/verify/resend/invite/MFA; AI quota 20/min per user, 1000/day per shop, input caps; boot warnings for Redis/proxy. |
| C13 billing | **Open — planned** | Launching paid, not a free beta. Until Stripe Billing lands, trials don't expire and Starter/Pro limits aren't enforced; don't take real sign-ups before it ships. |
| Auth | Fixed | Logout revokes the session (`revoked_sessions`); signup password rules; lowercase emails + `uq_employees_email_lower`; own email/password change needs current password; cross-shop invite → 409. |
| Data | Fixed | Status change + commissions in one transaction; invoice number unique per shop; `payment_intents.payment_id` unique; 13 indexes; PO/intent/feed/archived lists paged; first-sequence race fixed. |
| Also | Fixed | App `robots.txt` + `X-Robots-Tag: noindex`; fonts self-hosted (app, legacy pages, website — no Google requests); `.env.example` documents required settings; Node 26 test storage shim. |

Still open beyond C13: MFA secret encryption and code-to-disable, DOMPurify, SSRF pinning for
automation webhooks / tenant SMTP host, shop account closure, optimistic locking, remaining Float
money columns, earlier-QA UX items F1–F4/F6–F10, the `/tech` icons that never render (pre-existing),
turning on production backups (PITR) and S3 media, and getting `main` CI green.

---

## 1. Launch readiness score: **48 / 100** (before fixes)

The foundations are better than most apps at this stage: tenant isolation held under 158
cross-tenant requests, webhooks are signed and fail closed, money is mostly `Numeric`, config
refuses to boot with weak secrets, there are ~1,600 backend tests. What holds the score down is a
handful of *verified* defects in the core flow and in money handling, plus missing launch basics
(legal pages, real email, website links, mobile layout).

| Area | Score | Why |
|---|---|---|
| Security & secrets | 7/10 | No secrets anywhere (incl. full git history); solid headers, uploads, SQLi. Stored XSS in legacy `/tech` + token in `localStorage`; rate-limit design can lock out shops. |
| Auth & accounts | 6/10 | Login/reset/MFA/lockouts well built. Logout doesn't revoke; no password policy or email verification at signup; no re-auth for email/password change. |
| Tenant isolation / roles | 9/10 | 0 leaks in 158 cross-tenant requests; escalation attempts all refused. |
| Data integrity | 4/10 | Duplicate-status race breaks estimates for new shops; PO numbers collide across shops; core order-with-items create fails. |
| Payments | 5/10 | Webhooks, idempotent capture, server-side amounts mostly right. Stale pay-link amounts, revoked links still payable, wrong success URL; no SaaS billing at all. |
| Deployment | 5/10 | Good Dockerfile, health, Sentry, rollback docs. Config doesn't fail closed on SQLite / missing SMTP / PUBLIC_BASE_URL; reset links printed to logs. |
| Website / SEO / legal | 3/10 | SEO solid. App buttons point to `localhost:8000`; no privacy policy, terms or refund policy. |
| UI / mobile | 4/10 | (from earlier QA) web app unusable on phones; several confusing states. |

**Could not test (needs manual / staging):** real email/SMS/WhatsApp delivery; Stripe/Square in
test mode end-to-end; real-device camera scanning, push, PWA install, offline sync; load with
thousands of jobs and many concurrent staff; production proxy/IP setup; Core Web Vitals on real
networks; screen-reader pass; Python dependency CVEs (`pip-audit` not installed); the `tekly.app`
domain itself; Postgres production data compatibility of the money migration.

---

## 2. Critical blockers (must fix before real users)

| # | Problem | Evidence | Where |
|---|---|---|---|
| C1 | **Creating a work order with any item fails** (422 "Field required"). The core flow of the product. | Reproduced on SQLite and Postgres. Drawer sends `kind`, API requires `line_type`. | `frontend-react/src/pages/orders/CreateOrderDrawer.tsx:154`, `backend/routers/orders.py:66` |
| C2 | **New shops get duplicate statuses, then every estimate is a 500 forever.** | 10 parallel first loads → 38 duplicated keys on Postgres; `POST /api/orders {is_estimate:true}` → `MultipleResultsFound`. No unique index on `order_statuses`. | `backend/routers/statuses.py:118-169`, `orders.py:806,820,1242` |
| C3 | **Purchase-order numbers are unique across the whole platform.** Shop B's first PO is a 500 once shop A has `PO-00001`. | Reproduced on SQLite + Postgres. `po_number unique=True`, no `org_id` on `purchase_orders`. | `backend/models/__init__.py:998` |
| C4 | **Stored XSS → account takeover in the legacy technician app.** Customer names (settable from the public booking page), complaints and scanned codes go into `innerHTML` unescaped; CSP allows inline script; token is in `localStorage`. | No escape helper in the file; e.g. `` box.innerHTML = `…Looking up "${code}"…` `` | `frontend/static/js/tech/app.js:252-262,305+`, `dashboard.js` |
| C5 | **Password-reset / verification / invite emails are never delivered in production unless SMTP is set — and the links are printed to logs, reported as "sent".** Config doesn't require SMTP. | `SimulatedAdapter.send` uses `print()` (bypasses log redaction), returns `status:"sent"`; no `is_deployed` guard. | `backend/services/notifications.py:45`, `backend/config.py:191-239` |
| C6 | **Production can boot on an empty SQLite file** (default `DATABASE_URL`) with health still 200; `PUBLIC_BASE_URL` empty makes every emailed link relative. | Fail-closed validator checks neither. | `backend/config.py:191-239`, `backend/routers/auth.py:24-26` |
| C7 | **Pay links can charge the wrong amount or be paid twice.** A reused link keeps its original amount after a part-payment or total change; revoke/regenerate doesn't expire the Stripe/Square checkout, and a payment on a cancelled intent is still accepted. | Code-verified. | `orders.py:2097-2110`, `payments.py:119-135`, `services/payments.py:256-260` |
| C8 | **Customers are sent to `https://app.repairos.dev/pay/success` after paying** (old domain; and `/pay/success` doesn't exist — it renders a broken "Payment request"). QBO redirect same. | Defaults in config, not in `.env.example`, not enforced. | `backend/config.py:88-89,104`, `main.py:319` |
| C9 | **Marketing site's Sign in / Start free buttons go to `http://localhost:8000`;** demo form falls back to `mailto`. | `appUrl: "http://localhost:8000"`, `demoEndpoint: ""` | `website/site.config.js:7,13` |
| C10 | **No privacy policy, terms of service or refund policy anywhere**, no consent at signup — while collecting shop + customer PII and sending it to Anthropic, Stripe, Twilio, Meta, S3. | Grepped all website pages and `AuthPages.tsx`. | `website/`, `frontend-react/src/public/AuthPages.tsx` |
| C11 | **The web app is unusable on a phone and clipped on iPad** (sidebar never collapses). | Earlier QA B3; `Shell.tsx` unchanged since. | `frontend-react/src/components/Shell.tsx` |
| C12 | **Global rate limit can lock out a shop — or the whole platform.** 600 req/min per IP, page assets counted, not per user; Docker default `FORWARDED_ALLOW_IPS=127.0.0.1` makes every client look like the load balancer; per-worker counters without Redis. | `main.py:160`, `backend/core/observability.py:94`, `Dockerfile` | |
| C13 | **Decision needed: Tekly doesn't bill shops.** No subscription billing; signup sets `sub_end=None` so trials never expire; website's plan limits ("5 team members", Pro features) aren't enforced. | `backend/routers/auth.py:194`, `services/auth.py:75-85` | Launch as free beta, or build billing + gating first. |

## 3. Important fixes (soon; not all block a soft launch)

- **Logout doesn't revoke the session** — the token still works on `/me` and `/refresh` after logout; tokens live 24h and slide to 30 days. `backend/routers/auth.py:393-404`.
- **Signup accepts a 1-character password**; reset/invite enforce 8. `auth.py:185`.
- **Email verification never enforced; emails case-sensitive** (`A@x.com` and `a@x.com` are two accounts); `/register` not rate-limited → squatting and enumeration.
- **Changing own email/password needs no current password**; email change doesn't reset verification or revoke sessions. `backend/routers/staff.py:196`.
- **Order status change isn't one transaction** — status + ledger commit, then payroll accrual commits separately; a failure leaves a closed order without commissions. `orders.py:~1525-1576`, `services/payroll.py:356`.
- **Staff checkout takes the amount from the client** (capped only `> 0`). `payments.py:55-77`.
- **Gateway timeouts return 500** (only `RuntimeError` caught), including the public refresh endpoint. `payments.py`, `services/payments.py:72,105,135,413,422`.
- **Simulated gateway is still the production fallback** for shops without keys → customers get un-payable links. `services/payments.py:189-198`.
- **No AI usage limits** per org/user and no input length caps; any signed-in user can run up the Anthropic bill. `backend/routers/ai.py`, `comms_ai.py`. Missing key silently serves canned answers.
- **Twilio/Square webhook signatures will fail behind a TLS proxy** unless proxy headers / `PUBLIC_BASE_URL` are right. `chats.py:201`, `payments.py:200`.
- **No per-endpoint rate limits** on register, password reset, email verify/resend, invite accept, MFA activate, AI. `backend/routers/auth.py`, `mfa.py`, `ai.py`.
- **Missing unique constraints**: `invoices.invoice_number`, sale/transfer numbers, `payment_intents.provider_ref`/`payment_id`, `stock_levels(part, location, warehouse, bin)`. First-ever document numbering for a shop can race to a 500 (`services/numbering.py:97-125`).
- **Missing indexes** on `event_feed_items.org_id`, `order_statuses.org_id`, `client_returns.*`, `feedback.*`, `employee_balance_ledger.org_id`, `po_lines.po_id`, `sales.client_id`, `devices.client_id`, `payment_intents.invoice_id/payment_id`, `stock_movements.location_id`.
- **Unbounded lists**: `GET /api/inventory/po` (plus N+1), `GET /api/payments/intents`, per-entity feed, `/orders/archived?limit=` uncapped.
- **App pages are indexable** — no `robots.txt` on the app domain, no `X-Robots-Tag`; tokenised `/track`, `/portal`, `/pay` pages could be indexed if a link leaks.
- **Google Fonts loaded from Google** on website and app (GDPR exposure in the EU); GTM would load with no consent gate if `gtmId` is set. Self-host fonts; add consent before analytics.
- **No account closure / shop data deletion**; no employee erase (customer export/erase exists).
- **`main` CI is red** (last push 14 Oct failed CI + E2E). PR runs pass; Actions is *not* billing-blocked.
- **Backups**: documented in `DEPLOY.md` §4 but not set up; `PROD-HARDENING.md:22` unchecked; `MEDIA_LOCAL_DIR=/tmp` loses uploads on restart → use S3.
- **Earlier QA items still to do:** stall flags on every new job (F1), "Paid" never shown to customers (F2), booking page dark mode (F3), hard-coded "Workshop Central" (F5), POS shows profit to customers (F9), demo seed accounts can't sign in (F10).

## 4. Minor improvements

- Replace the hand-rolled HTML sanitizer with DOMPurify (`frontend-react/src/lib/safeHtml.ts`).
- Automation webhook isn't DNS-pinned (`services/automation.py:122-130` → use `pinned_target`); tenant SMTP host has no SSRF check (`org_creds.py:56`).
- Encrypt `mfa_secret`; require a TOTP code to disable MFA; platform-admin has no MFA and shares `SECRET_KEY`.
- Invite for an email that exists in another shop → 500 `IntegrityError` (`staff.py:437`, `auth.py:572`).
- `brand_custom_domain` not unique or verified (`/api/public/branding` can 500 or show another shop's brand).
- Track/portal tokens never expire; outbound webhook HMAC has no timestamp; Twilio inbound not de-duplicated by `MessageSid`.
- Seven money columns still `Float` (`wage_value`, `loyalty_point_value`, `po_approval_threshold`, `disc_*_value`, `Coupon.value`, `StocktakeLine.variance`); 72 raw `round(` calls left.
- No optimistic locking — concurrent edits to an order are last-write-wins; two stock paths are read-modify-write (`inventory.py:240`, `catalog_products.py:605`).
- `.dockerignore` patterns only match the root — `backend/test.db` would enter a locally built image (use `**/*.db` etc.). Keep `reset_admin.py` out of the image.
- `.env.example` lists ~25 of ~70 settings; add a `.env.production.example`.
- Stale config comment/warning about `META_APP_SECRET` (it *is* verified now).
- Dev-only npm vulnerabilities (vite, vitest, tailwind chain); prod deps are clean.
- docker-compose publishes default Postgres/pgAdmin passwords on `0.0.0.0` — bind to `127.0.0.1`, label "DEV ONLY".
- `AI_MODEL` default `claude-sonnet-4-6` — pick the launch model deliberately.
- Local Node 26 breaks `userPref.test.ts` (Node's own `localStorage` shadows jsdom); CI pins Node 22. Add `engines`/`.nvmrc`. ~240 stray `vite.config.ts.timestamp-*.mjs` files locally.
- Polish list from the earlier QA (layout shift on Home, duplicate fields, tap targets, phone-app nits).

---

## 5. Detailed checklist results

Legend: **PASS** verified · **FAIL** verified issue · **MANUAL** needs manual/staging test · **N/A**

### 1. Security & secrets
| Check | Result | Evidence |
|---|---|---|
| No secrets in frontend code | PASS | Key-pattern grep over `frontend-react/src`, `frontend/`, `website/`, built `dist`: no hits. |
| No secrets in git history | PASS | `git log --all -p` (31 branches) searched for live/test keys, AWS, private keys, Slack, GitHub, Google, Anthropic, Stripe webhook secrets: none. No `.db/.env/backup` ever committed. |
| Env vars configured correctly | FAIL | Fail-closed covers SECRET_KEY, CORS, TOKEN_ENCRYPTION_KEY, webhook secrets; not DATABASE_URL, SMTP, PUBLIC_BASE_URL, redirect URLs (C5, C6, C8). |
| Private endpoints require auth | PASS | All 852 routes listed; every unauthenticated one is intentionally public (auth flows, `/api/public/*`, signed webhooks, OAuth callbacks, token-gated metrics). |
| No IDOR | PASS | 158 cross-tenant requests + targeted read/write/delete tests: 0 leaks; ORM guard checks every FK on write (`backend/db/tenancy.py:43`). |
| Row-level security | N/A | App-level tenancy (no Postgres RLS); covered above. |
| SQL / command injection | PASS | No f-string/concatenated SQL in routers/services; no subprocess/eval/pickle; custom report columns allowlisted. |
| XSS | FAIL | Legacy `/tech` app unescaped `innerHTML` (C4); CSP `unsafe-inline`; token in `localStorage`. React app and website escape correctly. |
| File uploads | PASS | 8 MB cap, magic-byte sniffing, SVG/HTML forced to attachment with sandbox CSP, auth + org-scoped downloads, path traversal tested. |
| Rate limits | FAIL | Login/MFA/forgot/public booking limited; register, reset, verify, invite, AI not; global limiter design (C12). |
| CORS | PASS | `*` refused when deployed; credentials off with wildcard. |
| Debug endpoints / test accounts off in prod | PASS | Docs hidden, demo seed refused, simulate-pay 403, `reset_admin.py` refuses when deployed, no default platform admin. |
| Dependency CVEs | PASS (npm prod) / MANUAL (Python) | `npm audit --omit=dev`: 0. `pip-audit` not installed — run in CI. |

### 2. Authentication & accounts
| Check | Result | Evidence |
|---|---|---|
| Sign-up / login / logout work | PASS (logout FAIL) | Tested; logout leaves token valid (`auth.py:393-404`). |
| Password reset | PASS | 256-bit tokens hashed, 1h expiry, single use, sessions revoked after. |
| Email verification | FAIL | Sent but never enforced; emails case-sensitive. |
| Sessions expire / refresh | PASS (long) | 24h access, 30-day refresh; `logout-all-devices` revokes via `token_version`. |
| Protected pages after logout | FAIL | Server still accepts the token. Client drops it. |
| Admin features need permission | PASS | Escalation attempts all 403/401; platform admin is a separate table + token scope. |
| OAuth callbacks / redirects | PASS / MANUAL | QBO/Gmail/Outlook state/nonce OK; no open redirects. SSO has no callback yet (state not stored — must be added when built). |
| Errors don't leak | PASS (login, reset) / FAIL (register) | Login: same message + dummy bcrypt. Register: "Email already registered". |
| Account deletion | FAIL | Customer erase/export exists; no shop or employee deletion. |
| Works after refresh / new tab | MANUAL | Token in `localStorage` so expected yes; earlier QA saw no issues. |

### 3. Database & data integrity
| Check | Result | Evidence |
|---|---|---|
| Migrations run cleanly | PASS | `alembic upgrade head` on fresh SQLite and Postgres 16 (156 tables); single head `vd144q4`; `test_144eo_migrated_schema` 3/3. |
| Constraints prevent duplicates | FAIL | `order_statuses` (C2), PO numbers global (C3), invoice/sale/transfer numbers, provider refs, stock levels. |
| Foreign keys | PASS | No missing FKs in drift check; orphan-check migration aborts rather than drops. |
| Transactions | FAIL | Status change + payroll accrual split across commits. |
| No partial saves on failure | FAIL | Same; `ensure_seeded` commits mid-request. |
| Concurrent edits | FAIL (MEDIUM) | No optimistic locking; payments lock rows correctly. |
| Indexes | FAIL (MEDIUM) | ~12 missing on org/FK columns. |
| Pagination | FAIL (MEDIUM) | 4 unbounded list endpoints. |
| Backups | MANUAL | Documented, not configured. |
| Prod DB credentials | MANUAL | Not testable here; SQLite fallback risk (C6). |

### 4. Payments
| Check | Result | Evidence |
|---|---|---|
| Amounts server-side | PASS (pay links, invoices, POS) / FAIL (staff checkout) | `payments.py:55-77` trusts client amount. |
| Paid features via frontend state | FAIL / decision | No plan gating exists at all (C13). |
| Webhook signatures | PASS | Stripe, Square, Meta, Twilio verified, fail closed when deployed; Stripe 5-min tolerance + amount/currency/org checks. |
| Duplicate webhooks | PASS | Conditional-update claim; double confirm covered by `test_money_concurrency.py`. No unique index as a backstop. |
| Failed payments | PASS / FAIL | Failures shown; network errors are 500s. |
| Subscriptions | N/A today / decision (C13) | |
| Refunds consistent | PASS | Capped at net paid, row-locked, void blocks double void. Online refunds must be done in the provider dashboard (document it). |
| Test credentials not in prod | PASS | Simulated pay blocked when deployed; but simulated gateway still the fallback (important fix). |
| Success page ≠ proof | PASS | Page shows paid only after server confirms. |
| Stale / revoked links | FAIL | C7. |

### 5. API & integrations
| Check | Result | Evidence |
|---|---|---|
| Failures don't crash the app | PASS / FAIL | Notifications and webhooks degrade gracefully; payment gateway network errors → 500. |
| Timeouts | PASS | Every httpx/SMTP call has 4–60s timeout. |
| Retries don't duplicate | PASS | Outbox/delivery claims; notification rules claim before send. |
| AI usage limits | FAIL | None per org/user. |
| Webhooks processed safely | PASS | |
| Missing credentials → useful errors | PASS / FAIL | Clear 502/503 for payments; AI silently falls back to canned answers. |

### 6. UI & UX (from earlier QA today + this pass)
| Check | Result |
|---|---|
| Buttons work / no JS errors | PASS (≈500 clicks, 212 page loads) — except new-order-with-items (C1) |
| Form validation & errors | FAIL (new-order drawer errors below the fold, unnamed) |
| Mobile layout | FAIL (C11) |
| Loading / empty states, long text, keyboard, contrast | MANUAL / partial — tap targets < 24 px and unlabeled icon buttons found |
| Double submit | PASS for payments (idempotent); MANUAL elsewhere |
| Unsaved-work warning | MANUAL |

### 7. Error handling
| Check | Result |
|---|---|
| Useful errors, no stack traces | PASS (production hides traces; Sentry scrubs PII) |
| 404 pages | PASS in app ("link expired" pages); MANUAL on website |
| Server errors logged | PASS (JSON logs, redaction filter, Sentry opt-in) |
| Background jobs recover | PASS (advisory-lock sweeps on Postgres; claims) |
| Malformed / huge input | PASS (16 MB body cap, pydantic validation) — AI inputs uncapped |
| Offline / slow network | MANUAL (phone-app offline queue not tested) |
| Dates / currency | PASS mostly (Numeric money); float leftovers listed |

### 8. Performance
| Check | Result |
|---|---|
| Code splitting | PASS — route-level chunks; largest `SettingsHub` 424 KB (105 KB gz), main 292 KB (89 KB gz) |
| Fast pages (local) | PASS — < 150 ms pages, < 80 ms API (earlier QA, small shop) |
| N+1 / unbounded queries | FAIL — PO list |
| Layout shift | FAIL (minor) — Home CLS 0.15–0.21 |
| Core Web Vitals / realistic load | MANUAL |

### 9. SEO, accessibility & legal
| Check | Result |
|---|---|
| Titles, descriptions, canonical, OG, sitemap, robots (website) | PASS — 5 pages, `tekly.app` |
| Private pages not indexed | FAIL — no app `robots.txt` / `X-Robots-Tag` |
| Alt text / form labels (website) | PASS |
| Privacy policy / terms / refund policy | FAIL (C10) |
| Cookie consent | PASS today (no cookies, GTM empty) / FAIL once analytics added; Google Fonts IP transfer |
| Asset licences | PASS — fonts OFL/Apache; product screenshots own (confirm `.webp` provenance) |

### 10. Deployment
| Check | Result |
|---|---|
| Production env vars complete | FAIL (C5, C6, C8; `.env.example` incomplete) |
| Production build succeeds | PASS — `vite build` 8.6 s, `tsc --noEmit` clean |
| Automated tests pass | see §5a below |
| Migrations compatible with prod data | MANUAL — money Float→Numeric rewrite locks tables if prod predates it |
| Staging / prod separated | PASS (`is_deployed` covers both) |
| Dev features disabled | PASS |
| Logs don't expose secrets | FAIL — simulated email `print()` (C5); otherwise redaction filter PASS |
| Error monitoring | PASS (Sentry wired, off until DSN set) |
| Health checks | PASS (`/api/health` checks DB, 503 on failure) |
| Rollback plan | PASS (`DEPLOY.md` §3) |
| Tested against deployed version | MANUAL — nothing deployed to test |

### 5a. Test runs (this pass)
| Suite | Result |
|---|---|
| Backend pytest (full, isolated DB) | **1629 passed, 0 failed** (12 min, 6 workers). A first run sharing `./test_gw*.db` with other test processes gave 247 lock/contention failures — runs must not share the default DB files. Note the suite passes *while* C1–C3 are real: nothing tests the drawer payload, parallel first load, or two shops' POs. |
| Payment/webhook/refund/sale subset (isolated DB) | 185 passed, 0 failed |
| Cross-tenant + escalation scratch tests | 5/5 passed (0 leaks) |
| Frontend `tsc --noEmit` | PASS |
| Frontend build | PASS |
| UI-text lint | PASS |
| Vitest | 163 passed, 10 failed — 9 = Node 26 `localStorage` env issue (CI uses Node 22), 1 = known CashPanel flake under load |
| `npm audit --omit=dev` | 0 vulnerabilities |

### 11. Real-world scenarios
| Scenario | Result |
|---|---|
| New user signs up and completes main workflow | FAIL — order with items fails (C1); estimates may 500 (C2) |
| Returning user after session expiry | PASS (refresh/401 handled; earlier QA) |
| Two users, same private resource (cross-tenant) | PASS |
| Same form twice | PASS for payments; MANUAL elsewhere |
| Lose internet mid-action | MANUAL (phone app shows "no customer found" on errors — F7) |
| Invalid/unexpected data | PASS (validation, caps) |
| Main workflow on mobile | FAIL (C11) |
| Abandon and return | MANUAL |
| Third-party API fails | PASS for notifications/webhooks; FAIL for payment gateway timeouts |
| Role not permitted | PASS (Batch D enforcement + escalation tests) |

---

## 6. Fix plan (highest risk first)

Each step: change → test that proves it.

1. **C1 — order lines.** Make `LineIn.line_type` optional and derive it from `kind` (service→`labour`, product→`part`, custom/fee→`fee`) *and* send it from `CreateOrderDrawer.tsx`. **Test:** API test posting the drawer's exact payload → 201 with lines; vitest asserting the payload shape.
2. **C2 — status race.** Migration: delete duplicate `order_statuses` rows (keep lowest `sort_order`/oldest, repoint any orders), add `UNIQUE(org_id, record_type, key)` + `org_id` index. Seed all pipelines at registration; make `ensure_seeded` insert-if-missing ignoring conflicts; switch the 18 `scalar_one_or_none()` status lookups to `.first()`. **Test:** 10 parallel `GET /api/statuses` for a new org → 0 duplicates, estimate create → 201; migration test on a DB seeded with duplicates.
3. **C3 — PO numbers.** Add `org_id` to `purchase_orders` (backfill via supplier/location), replace global unique with `UNIQUE(org_id, po_number)`. **Test:** two shops each create their first PO → both 201, both `PO-00001`.
4. **C4 — legacy XSS.** Add `esc()` and wrap every interpolation in `frontend/static/js/tech/app.js` and `dashboard.js` (or render with `textContent`); then drop `'unsafe-inline'` from `script-src` once inline handlers are gone. **Test:** a client named `<img src=x onerror=alert(1)>` renders as text in `/tech` (Playwright); CSP header test.
5. **C5/C6/C8 — fail-closed config.** When deployed, refuse to boot with SQLite, empty `PUBLIC_BASE_URL`, empty `SMTP_HOST`/`SMTP_FROM`, or any `repairos.dev` default; derive Stripe success/cancel and QBO redirect from `PUBLIC_BASE_URL`; add a real `/pay/success` page; make `SimulatedAdapter` log (not print) and return `failed` when deployed. **Test:** settings unit tests for each refusal; pay-success route test; notification test asserting no token appears in captured stdout.
6. **C7 — pay links.** Reuse a live link only if `amount == balance_due`, else cancel + reissue; on revoke/regenerate call `_expire_stripe_checkout`/`_delete_square_link`; in `mark_succeeded`, a payment on a cancelled intent is recorded but flagged for refund review; add unique index on `payment_intents.payment_id`. **Test:** part-pay then reopen link → new amount; revoke then webhook success → flagged, not double-counted.
7. **C12 — rate limits.** Exempt `/assets`, `/static`, `/sw.js` and SPA HTML; key authenticated requests by user/org; require `REDIS_URL` when `WEB_CONCURRENCY>1`; warn at boot if forwarded-IP settings are defaults. Add per-route limits to register/reset/verify/invite/MFA/AI. **Test:** 700 asset requests don't 429 an API call; two users on one IP get separate buckets.
8. **C9/C10 — website & legal.** Set `appUrl` and `demoEndpoint`; publish Privacy, Terms, Refund, sub-processor list; footer links + signup consent checkbox; self-host fonts. **Test:** link checker on the built site; signup without consent → 422.
9. **C11 — mobile shell.** Collapse sidebar into a drawer below ~900 px (or route phones to `/go`). **Test:** Playwright at 390/820 px: main content ≥ 70% width on every top-level page.
10. **C13 — billing decision.** Either label launch as free beta (and remove plan limits from the site) or add Stripe Billing + trial expiry + seat/feature gating. **Test:** trial org past `sub_end` is read-only.
11. **Auth hardening.** Logout bumps a per-session revocation (session table / `jti`) or at minimum shorten access tokens to ≤60 min; min password length at signup; lowercase emails + case-insensitive unique index; require current password for email/password change and bump `token_version`. **Test:** token after logout → 401; `A@x.com` vs `a@x.com` → 409.
12. **Data hardening.** One transaction for status change + accrual; unique constraints and indexes listed in §3; cap unbounded lists; S3 media; turn on PITR backups and run one restore drill. **Test:** drift gate, fault-injection test that accrual failure rolls back the status change.
13. **Get `main` CI green**, add `pip-audit`, then run a staging deploy with real SMTP and Stripe/Square test mode and walk §11 scenarios on real devices.

## 7. Final verdict: **NOT READY TO SHIP**

The core "create a work order with items" flow fails from the web form, new shops can permanently
lose the ability to create estimates, purchase orders collide across shops, payment links can
charge stale amounts or be paid twice, and production would silently not send password-reset or
verification emails. There are also no legal pages and the website's app buttons point to localhost.

None of these are architectural: the security model, tenancy and money handling underneath are
sound. Steps 1–9 of the fix plan are each small, testable changes; after them (and a staging pass
with real email and payment test mode) this becomes **READY AFTER FIXES**.
