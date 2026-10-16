# RepairOS — Production-Readiness Audit (2026-10-14)

Audited commit: `f499696` (branch `main`). Read-only audit: no source file, config, database,
or infrastructure was changed. The only file created is this report (untracked).

**Bottom line: not production-ready.** The core is solid (clean migrations, 1,153 passing tests,
good auth fundamentals), but there are **2 critical** and **~15 high** issues — one of them lets
an unauthenticated attacker download the database and the server's secret key. Fix Phase 0
before this runs anywhere reachable from the internet.

> ⚠️ **Urgent, outside the code:** `github.com/Dante724/SAAS` is currently **public** and contains
> this full source (`repairos-source.zip`). Combined with C1 below, anyone can find and exploit
> it. Make that repo private again now.

---

## How this audit was done

- Four parallel reviews (auth/tenancy, money paths, input handling/integrations,
  DB/ops/performance), all 803 routes enumerated. Every Critical and most High findings were
  **re-checked by reading the code**; C1 was **reproduced** against the real app.
- **Confidence labels:** **CONFIRMED** = code path traced end-to-end (or reproduced).
  **POTENTIAL** = risky pattern, exploitability not proven. **ASSUMPTION** = depends on how you
  deploy, which I could not see.

### Checks run (all against throwaway DBs in a scratch folder, never `repairos.db`)

| Check | Result |
|---|---|
| `ruff` (CI scope) | ✅ clean. (Whole backend: 66 issues incl. 1 `F821 undefined-name` — CI doesn't lint `routers/`/`services/`) |
| `alembic upgrade head` from empty (SQLite) | ✅ single head `fk144ep01` |
| Model/migration drift | ✅ per project's gate. 78 columns are nullable in DB but NOT NULL in models (tolerated by design; incl. tenant column `checklist_sections.org_id`) |
| Backend `pytest` (xdist) | **1153 passed, 1 failed** (`test_ssrf_allows_public` — needs live DNS; passed on an earlier run → flaky) |
| Backend coverage | ❌ **49.8%** vs 50.0% floor → `make ci-backend` is red |
| Frontend `tsc`, ui-text lint, `vite build` | ✅ (main chunk 1.32 MB, no code splitting) |
| Frontend `vitest` | ✅ 85/85 on Node 20 (CI). ❌ 9 fail on local Node 26 (Node's built-in `localStorage` shadows jsdom's) |
| `pip-audit` (installed env) | ✅ no known vulns |
| `npm audit --omit=dev` | ❌ `xlsx` (high, no fix on npm), `react-router` (moderate, open redirect) |
| Local `.venv` | ⚠️ stale — missing `pytest-xdist`/`pytest-cov`; `make test` fails until `make install` |

---

## 1. Critical security & data-loss risks

### C1 · Unauthenticated arbitrary file read (path traversal) — CRITICAL · CONFIRMED (reproduced)
- **Evidence:** `main.py:307-321` `serve_spa(path)` does `os.path.join(react_dir, path)` /
  `os.path.join(frontend_dir, path)` → `FileResponse` with no normalisation or containment.
  `GET /..%2f..%2fREADME.md` returned the repo-root README (200). `//etc/hosts` and absolute
  paths also work (`os.path.join` discards the base for absolute paths).
- **Impact:** anyone can download `repairos.db` (+ the `.bak`/backup DBs in the repo root),
  `.env`, `/proc/self/environ` → `SECRET_KEY` → forge a JWT for **any user in any tenant**, and
  decrypt every tenant's stored Stripe/Square/Twilio/Meta credentials (Fernet key is derived from
  `SECRET_KEY`, `services/org_creds.py:76`). Full multi-tenant compromise.
- **Fix:** resolve with `os.path.realpath` and require the result to be inside
  `realpath(react_dir)`/`realpath(frontend_dir)`; reject `..`, leading `/`, `\`. Prefer
  `StaticFiles(html=True)` for files and keep the fallback for the SPA shell only. If this build
  was ever internet-exposed: **rotate `SECRET_KEY` and all stored integration credentials.**

### C2 · Forged "payment succeeded" via Stripe/Square webhooks — CRITICAL · CONFIRMED (code)
- **Evidence:** `routers/payments.py:245` verifies the Stripe signature **only**
  `if settings.STRIPE_WEBHOOK_SECRET` (global env); otherwise "the raw event is trusted".
  Square same at `:169`. Shops store their own keys incl. a `webhook_secret`
  (`services/org_creds.py:35`) which is **never read**. The prod fail-closed check
  (`config.py:162-170`) only fires when the *global* `STRIPE_SECRET_KEY` is set.
- **Impact:** in the normal "shop brings its own Stripe key" setup, anyone holding a pay-link id
  (every customer has theirs) can POST `checkout.session.completed` → transaction + payment
  posted, invoice marked paid, loyalty earned — no money moved. Even with a valid signature
  there's no check of `payment_status`, amount, currency, or that the intent's provider/org
  matches. Also: if the global secret *is* set, real per-shop events fail verification.
- **ASSUMPTION:** severity assumes per-shop credentials are used in production (the code is
  built for it).
- **Fix:** load intent → verify with that org's secret (global as fallback) → fail closed if
  none (outside dev/test) → check provider, `payment_status=="paid"`, amount & currency.

---

## 2. High-severity security issues

| ID | Issue | Status | Evidence | Impact | Fix |
|---|---|---|---|---|---|
| H1 | **Default password `changeme123`** for every employee created in the React UI | CONFIRMED | `routers/staff.py:27` default; React form (`SettingsHub.tsx` ~1797) never sends `password` | Anyone who knows/guesses a staff email logs in | No default; create without password + send invite/reset link; force reset for existing accounts |
| H2 | **Privilege escalation to owner** | CONFIRMED | `require_perm` treats `role_id is None` as owner (`services/auth.py:103`); `update_employee` (`staff.py:152-172`) blindly `setattr`s `role_id`, `email`; admin password reset (`:176`) min 6 chars, no `token_version` bump; `/reset-mfa` on anyone | Any user with `can_manage_employees` (incl. default "Manager") → `role_id:null` = full owner; can reset owner's password/MFA/email | Gate role changes on `can_manage_roles`; same-org role; forbid null role & acting on higher-privileged users/owner; bump `token_version` |
| H3 | **RBAC mostly not enforced** | CONFIRMED | 57 of 77 catalog permission codes are referenced nowhere in the backend (e.g. `can_create_invoices`, `can_set_discount`, `can_view_payroll`); enforced codes (`can_view_finance`, …) aren't in the catalog, so the UI can't deny them (`rbac.py:84`). 451 routes need only login. Examples: `clients.py:913 move_balance`, `finance.py record_payment/create_invoice/shifts/expenses`, `statuses.py:346/360` transition rules, `reports.py` (payroll/profit export), `staff.py:105` lists wages & tax ids, `webhooks.py:16` returns signing secrets | Role restrictions the owner configures largely do nothing | Map each code to endpoints; add catalog entries; CI test: every non-GET route has `require_perm` or is allow-listed |
| H4 | **Stored XSS → session theft (media uploads)** | CONFIRMED (code) | `routers/media.py:45` accepts client `mime`; `:66` serves it back as-is, no `Content-Disposition`; React opens via same-origin blob URL (`api/client.ts:80`). Token in `localStorage.ros_tok`; CSP allows `'unsafe-inline'` | Employee uploads HTML/SVG attachment → owner clicks → token stolen | MIME allowlist + magic-byte sniff; never serve html/svg; `attachment` + sandbox CSP |
| H5 | **Stored XSS via print templates / record data** | CONFIRMED (code) | Template CRUD needs only login (`documents.py:351,370,394`); `_render` substitutes client name/complaint etc. **unescaped**; rendered into un-sandboxed `iframe.srcdoc` (`BulkPrintDialog.tsx:56`, `TemplateEditorDrawer.tsx:271`, `InventoryHub.tsx:104`), `dangerouslySetInnerHTML` (`TemplateEditorDrawer.tsx:439`), `document.write` (`WODrawer.tsx:183`). POTENTIAL unauthenticated path: public booking → lead → client name | Token theft when staff print | `html.escape` all variables; sanitize `body_html` (nh3); `sandbox=""` on print iframes; gate templates on `can_manage_settings` |
| H6 | **Rate limits bypassable via `X-Forwarded-For`** | CONFIRMED | `services/ratelimit.py:38-43`, `core/webhook_security.py:77` trust first XFF hop | Unlimited password & TOTP guessing; unbounded limiter memory | Trust XFF only from configured proxy; per-account lockout; MFA attempt counter + TOTP replay guard |
| H7 | **SSRF via tenant outbound webhooks** | CONFIRMED | `services/webhooks.py:69` posts with no `assert_safe_url`; create checks only scheme (`routers/webhooks.py:42`), PATCH none (`:66`). `PROD-HARDENING.md` claims otherwise | Tenant admin probes/POSTs internal services & cloud metadata; errors stored = oracle | `assert_safe_url` on save + each delivery; no redirects; pin resolved IP; generic errors |

## 3. Medium security issues

| ID | Issue | Status | Evidence → Fix |
|---|---|---|---|
| M1 | **Cross-tenant references by id** (needs victim's UUID) | CONFIRMED code path | Body `*_id`s not checked against org: `orders.py:709` client/device (then leaked by GET & `/notify` messages them), `finance.py:334` invoice client (+ `services/ledger.py:68` unscoped balance write), `stock_docs.py:639` supplier (writes other tenant's AP), `tasks.py`, `v1.py:172,236`, `inbox.py:219`, `telephony.py:67`, `assets.py`, `appointments.py`, `returns.py:35`. → shared `ensure_owned(db, Model, id, org_id)` + org filter in `post_contact_ledger` |
| M2 | **Org-mandated MFA only enforced in the browser** | CONFIRMED | `auth.py:329` returns a full token; `get_current_employee` never checks. → limited-scope token until enrolled |
| M3 | **"Own records only" scopes not enforced** on 27 order routes and most client routes | CONFIRMED | `_guard_order_access` (`orders.py:1463`) missing on change_status, lines, assign, notify, archive…; tech can self-assign any order. → central scoped loaders |
| M4 | **Inbound Meta/Twilio webhooks unsigned** unless global secret set | CONFIRMED | `inbox.py:354-370`, `chats.py:180-200`; prod only warns. → fail closed; per-org secrets |
| M5 | **Staging runs fail-open** | CONFIRMED | Prod checks only when `ENVIRONMENT=production`: placeholder `SECRET_KEY`, `CORS=*`, `/docs`, unauthenticated `simulate-pay`, seeded platform admin `admin@platform.local` (`platform_auth.py:69-75`), runtime `create_all` on Postgres. → apply prod rules to all but dev/test |
| M6 | **OAuth tokens stored plaintext if `TOKEN_ENCRYPTION_KEY` unset** — not required in prod | CONFIRMED | `services/crypto.py:32-40`; `SECRET_KEY` reused for JWT + Fernet + QBO HMAC; SSO `client_secret` unencrypted (`sso.py:57`). → require key, separate keys, MultiFernet rotation |
| M7 | **Reflected XSS on QBO OAuth callback** | CONFIRMED | `integrations_qbo.py:38` echoes `realmId` into HTML. → escape/validate `^\d+$` or redirect |
| M8 | **Stored XSS in public customer portal** (legacy page still served) | CONFIRMED | `frontend/portal.html:72-133` `innerHTML` with order/client fields; `esc()` exists but unused. → escape or port to React |
| M9 | **Custom reports open to all roles** (client PII, costs) | CONFIRMED | `custom_reports.py:53,103`. Query builder itself is safe. → `require_perm` |
| M10 | reportlab markup injection in PDFs (local image embed / fetch) | POTENTIAL | `services/printing.py:191-207`. → `xml.sax.saxutils.escape` + restrict `trustedHosts` |
| M11 | SSO: unverified domain claims, arbitrary `redirect_uri`, no state/nonce/PKCE | POTENTIAL (feature unwired) | `sso.py:50-116`. → fix before enabling |

**Low:** CSV/XLSX formula injection in exports (`clients.py:830`, `reports.py`, `catalog_products.py:362`);
no upload size/zip-bomb limits (`catalog_products.py:375`, `/api/media` decodes before size
check); `/metrics` unauthenticated; `/api/readyz` returns DB error text; timing-unsafe token
compare (`webhook_security.py:40`); `/auth/refresh` gives indefinitely sliding sessions; API keys
ignore subscription suspension; no HSTS; capability tokens (`/track/`, `/portal/`) in access logs.

---

## 4. Bugs & broken features (money integrity first)

All CONFIRMED by code trace unless noted. "Any user" = only login required.

| ID | Bug | Evidence | Failure scenario | Fix |
|---|---|---|---|---|
| B1 | **Refund cap is ineffective** | Cap sums `Payment(kind="refund")` (`returns.py:92-99`) but `create_refund` writes a `Refund` row, never a Payment; cap skipped if no `order_id` | Refund a paid 100 order N times → till drained 100·N; refund then archive-with-refund → refunded twice | Count `Refund` rows; lock order row |
| B2 | **Payment links always charge USD** | `orders.py:1840` `currency="usd"`; ×100 minor units for all currencies | INR ₹5,000 → Stripe checkout **$5,000**; JPY wrong ×100 | Org/document currency; per-currency exponent |
| B3 | **Customers can be charged twice** | Pay-link/tech/engage balance = `total - prepayment_total` (`orders.py:1788,1821`, `tech.py:258`, `engage.py:153`); only prepayments update it | Paid in-store → pay link still shows full balance | Balance from `sum(Payment)` net of refunds; revoke open intents at 0 |
| B4 | **Discount approval & margin guard bypass** | Fixed-amount discounts skip approval (`orders.py:2920`); revenue ≤ 0 → margin `None` → never blocked (`pricing.py:228`); client `cost_price` overrides catalog (`:207`); no clamps on qty/price/pct in `_calc_line` | Restricted user gives 100% discount | Normalise to %, block revenue ≤ 0, ignore client cost, validate ranges |
| B5 | **POS accepts negative qty/price** | `sales.py:188`; stock guards pass for negative qty; Part lookup unscoped (`:191`) | `qty:-3` adds phantom stock; `price:-500` zeroes a sale | Validate; same guards as orders |
| B6 | **Archive → restore → archive restocks twice** | `_restore_order` (`orders.py:2403`) doesn't re-deduct | 2 screens used → 4 phantom units | Per-line `stock_reversed` flag or re-deduct on restore |
| B7 | **Invoice payments accept negative amounts** (any user) | `finance.py:493-540`; cash leg posts `abs(amount)`; Payment has no `order_id` | `-100` → cashbox +100; invoice paid via finance but order still "due" → collected again | `amount > 0`, perm, set `order_id`, cap |
| B8 | **Orders with >1 invoice crash payment** | `scalar_one_or_none()` on Invoice by order in `cash.pay_order`, `payments.create_intent`, `mark_succeeded`; sub-invoices exist | 500 `MultipleResultsFound` on pay / pay link | Pick explicit invoice; recompute totals |
| B9 | **Duplicate invoice numbers** | Three numbering schemes (`numbering.py:116` unlocked, `finance.py:331` count+1, `invoicing` count+1); no unique constraint | Two invoices both `INV-00005` | One `next_number` with row lock + `UNIQUE(org_id, invoice_number)` |
| B10 | **Till close hides shortages** | `_expected_cash` (`finance.py:567`) ignores WO cash payments (no `shift_id`, type `invoice_payment`) | 200 short shows as +300 over | Set `shift_id`/count those types |
| B11 | `refund_overpayment` uncapped, no sign check | `cash.py:675-700` | Any amount out of till | Cap at real overpayment |
| B12 | Gift card redemption uncapped; coupon `uses += 1` race; loyalty double-spend on orders | `engage.py:347` etc. | Negative order total → cash out via B11 | Mirror POS guards |
| B13 | Voiding a payment's cash leg desyncs documents | `cash.delete_transaction` (`:327`) | Till reversed, invoice still paid | Block or cascade |
| B14 | Archive refund leaves phantom store credit | `orders.py:2338` no `+refund` ledger row | Client shows −100 credit after cash returned | Post refund to ledger |
| B15 | Supplier returns/write-offs accept negative qty & client cost | `stock_docs.py:209,376` | Return 1 unit at cost 10000 → 10000 "income" | `qty > 0`, server-side cost |
| B16 | Two stock-posting engines diverge | `stock_docs._apply_posting_line` vs `stock_posting.apply_receipt_line` (2dp vs 4dp avg cost, no batches, cash settlement posts nothing) | Wrong COGS/valuation | Route through one engine |
| B17 | Float compare marks invoices "partial" | `cash.py:560,613`, `services/payments.py:280` (`100.1+200.2 >= 300.3` is False) | Fully paid invoice stays partial | `q2()` before compare |
| B18 | Unlocked read-modify-write on balances & stock | `cash.py:68`, `ledger.py`, `_take_stock`… | POTENTIAL lost updates under concurrency | Atomic `UPDATE … SET x = x + :d` / `FOR UPDATE` |
| B19 | `mark_succeeded` not locked; posts no client-ledger entry | `services/payments.py:235` | POTENTIAL double payment under concurrent webhook/refresh; client balance wrong after online pay | Conditional update + ledger post |
| B20 | Foreign-currency cash accounts receive base amounts unconverted | pay/refund paths | Wrong balances | Reject or convert |
| B21 | PO receive allows over-receipt, unlocked | `inventory.py:1606` | POTENTIAL doubled stock/AP | Cap + lock |
| B22 | **Legacy fallback `/` is a blank page** | `main.py:204-209` serves `frontend/index.html` which loads a non-existent `/assets/index-QKSFrutK.js` | If React `dist/` wasn't built, users get a blank app | Fail boot if `dist` missing in prod; delete fallback |
| B23 | Flaky test needing live DNS | `test_security_144cq.py::test_ssrf_allows_public` | Random CI red | Mock `getaddrinfo` |

---

## 5. Production blockers

| ID | Blocker | Status | Fix |
|---|---|---|---|
| P1 | **No deployment artifact** — no Dockerfile/Procfile/process config; React `dist/` only exists if someone builds it | CONFIRMED | Multi-stage Dockerfile (build React → Python image), documented `uvicorn --workers --proxy-headers --forwarded-allow-ips` |
| P2 | **Periodic jobs run in every worker/replica, no leader lock, no row claiming** (`core/jobs.py:76-81`, `notification_rules.py:252-271` commits once at end) | CONFIRMED | Postgres advisory lock per sweep or single scheduler process; claim rows; commit per message → else duplicate SMS/emails/webhooks |
| P3 | **No backups / restore runbook**; `MEDIA_BACKEND=local` defaults to `/tmp`; `s3` backend needs `boto3`, not in requirements | CONFIRMED | Managed Postgres PITR + tested restore drill; object storage for media |
| P4 | **Side effects before commit** — SMS/email/webhooks sent inside the request before `db.commit()` (`orders.py:1273` vs `:1313`, `services/webhooks.py:30-56`) | CONFIRMED | Transactional outbox, dispatch after commit |
| P5 | **No Python lockfile** — `requirements.txt` is `>=` floors only; local env already on starlette 1.6/fastapi 0.141 | CONFIRMED | `uv lock`/pip-tools with hashes |
| P6 | **CI gaps** — Postgres job is `continue-on-error`; no pip-audit/npm audit/secret scan; ruff skips routers/services; coverage gate currently red; e2e workflow never builds React | CONFIRMED | Make Postgres required; add scanners; extend lint |
| P7 | Runtime DDL/backfills on every boot outside `production` (incl. staging Postgres); `RUN_MIGRATIONS_ON_STARTUP` runs `create_all`, not Alembic (`db/database.py:113-203`) | CONFIRMED | Gate on `is_sqlite`; flag → `alembic upgrade` |
| P8 | `try/except` around DDL in migrations leaves Postgres transactions aborted (`ix144an01`, `pt144cu01`, `b7d9f1…`, `jk144df01`, `z6b8d0…`) | POTENTIAL (fine on fresh DB) | Inspector-based `IF [NOT] EXISTS` |
| P9 | GDPR erase/export incomplete (`compliance.py:108` clears 4 client fields; appointments, calls, notification logs, chats, leads, media untouched) | CONFIRMED | Full PII map + tests |
| — | Plus C1, C2, H1–H7, M2, M5, M6 above | | |

## 6. Performance & scalability

| ID | Concern | Status | Fix |
|---|---|---|---|
| F1 | Product list unbounded + 2 queries per product (`catalog_products.py:234-259`) | CONFIRMED | Pagination + aggregate queries |
| F2 | Client list: no limit cap, eager-loads all orders & devices per client (`clients.py:87-99`); orders list up to 1000 with selectin graphs; ~36 GET endpoints query in loops | CONFIRMED | Caps, slimmer list serializers |
| F3 | CPU work on the event loop: reportlab PDFs, openpyxl import, bcrypt in login, sync DNS & redis; `print_labels` unbounded ids × 100 copies (DoS) | CONFIRMED | `asyncio.to_thread` + caps |
| F4 | 42 of 154 tables with `org_id` lack a leading index (e.g. `audit_log`, `employees`, `notification_logs`); 177 FK columns unindexed | CONFIRMED (from metadata) | Composite `(org_id, …)` indexes, `CONCURRENTLY` |
| F5 | Global rate limiter is per-process, and behind a proxy all users share one bucket; Redis fallback disables itself permanently after one failed ping | CONFIRMED | Redis-backed, proxy-aware |
| F6 | Frontend main chunk 1.32 MB, zero lazy routes | CONFIRMED | Route-level `lazy()` |

## 7. Code quality & maintainability

- **Duplicated logic:** two stock-posting engines (B16); three invoice-numbering schemes (B9);
  balance computed four different ways (B3); two frontends (React + legacy still live at
  `/admin`, `/tech`, `/pay`, `/portal`).
- **Dead code tracked:** 30 orphan bundles in `frontend/assets/` (still publicly served), 97
  legacy `frontend/tests/*.test.js` no CI runs, 71 files in `frontend/static/js`, two
  `.fuse_hidden*` files (~208 KB each), deprecated `backend/requirements.txt` shim.
- **Giant files:** `routers/orders.py` 3,565 lines; `models/__init__.py` 3,194 (154 models);
  `SettingsHub.tsx` 3,638; `CHANGELOG.md` ~650 KB.
- **Lint scope:** `routers/` and `services/` aren't linted; whole-backend ruff finds 66 issues
  incl. one undefined name (`F821`) — worth a look.
- **Python 3.9 target** in ruff vs CI 3.11 vs local 3.13.
- **Repo hygiene (local only):** untracked `extract/` and `SAAS/` folders (copies from my
  earlier unzip — safe to delete), stray `test_*.db`, `_wtest.db`, `pytest-cache-files-*` in the
  repo root (ignored by git but readable through C1).

## 8. Missing tests & observability

- **Coverage:** 49.8% overall, ~20% branch. Lowest where it matters most: `services/webhooks.py`
  23%, `stock_posting.py` 23%, `xero.py` 27%, `notifications.py` 35%, `ledger.py` 39%,
  `payments.py` 43%; per `TESTING.md`, `apikeys.py`/`v1.py` 0%, `public.py` ~10%,
  `cash`/`finance`/`sales` 7–13%. Frontend line coverage ~2.8%.
- **No tests for:** path containment, webhook signature enforcement with per-org creds,
  authorization matrix (route → permission), cross-tenant body-id rejection, refund caps,
  negative amounts, concurrent payment/stock updates, multi-worker scheduler behaviour.
- **Observability:** no Sentry/OTel/alerting; `/metrics` unauthenticated and per-process;
  structured JSON logs and request ids exist (good) but log capability tokens in paths and echo
  unvalidated `x-request-id`; 5 routes return raw `str(e)`.

## 9. Recommended improvements & development opportunities

- Move the session token from `localStorage` to an HttpOnly, SameSite cookie and drop
  `'unsafe-inline'` from the CSP — this defangs every XSS above in one stroke.
- Replace `python-jose` with PyJWT; replace `xlsx` (npm, abandoned) with `exceljs` or SheetJS CDN
  build; upgrade `react-router` (≥ 6.30 / 7.x), vitest 3, vite 6.
- Serve public pages (portal, pay, booking) from a separate origin/subdomain.
- Then resume the product roadmap (`ROADMAP.md`): Phase 1 "wire what's built" is frontend-only
  and high-ROI; i18n and the React PWA/technician app are the biggest regressions; AI-assisted
  onboarding/migration and the agentic shop assistant are the differentiators.

## Verified OK (no action needed)

Single Alembic head, no unsafe NOT NULL add-columns, FK migration uses NOT VALID + VALIDATE;
JWT algorithm pinned, `token_version` revocation, org suspension rechecked per request; reset /
verify / invite tokens 256-bit, hashed, single-use, no account enumeration; platform-admin
tokens scoped separately; Stripe signature check (when used) constant-time with 300 s replay
window; checkout amounts come from the server; `simulate-pay` blocked in production; cash
account ids org-scoped in `post_transaction`; media fetch org-scoped; custom-report query
builder whitelisted & parameterised; no raw SQL from user input; no SSTI; credentials Fernet-
encrypted and masked on read; prod refuses weak `SECRET_KEY` and wildcard CORS; `/docs` hidden in
prod; 500 handler leaks no traces; no secrets or databases in git history; `.gitignore` solid.

---

# Implementation plan

Ordered by risk × dependency. Each task = one small PR with tests; nothing merges without the
existing CI gates green. Database-changing tasks ship as Alembic migrations tested on SQLite
**and** Postgres.

### Phase 0 — Stop the bleeding (do first, ~1 day)

| # | Task | Acceptance criteria | Tests |
|---|---|---|---|
| 0.1 | Fix SPA path traversal (C1) | `/..%2f…`, `//etc/hosts`, `/%2F…`, backslash paths → 404; real React assets & client routes still served | Parametrised traversal tests (encoded, double-encoded, absolute, backslash) + happy-path asset test |
| 0.2 | Webhook fail-closed with per-org secrets (C2, M4) | Unsigned/invalid Stripe, Square, Meta, Twilio events → 400 outside dev/test; per-org secret verifies; amount/currency/provider/`payment_status` checked | Signed vs unsigned vs wrong-org vs wrong-amount cases for each provider |
| 0.3 | Remove `changeme123`; invite flow for new staff (H1) | Creating staff without password yields an unusable hash + invite email; existing default-password accounts flagged for reset (migration reports count, does not lock anyone out without your OK) | Create-employee test; login with old default fails |
| 0.4 | Block privilege escalation (H2) | Can't set null/higher role, can't edit owner or higher-privileged users; admin reset bumps `token_version`, 8-char min | Escalation attempts → 403 |
| 0.5 | Ops: make SAAS repo private; rotate secrets if anything was exposed | — | — |

### Phase 1 — Account & XSS hardening (~3–4 days)

| # | Task | Acceptance | Tests |
|---|---|---|---|
| 1.1 | Trusted-proxy IP + per-account lockout + MFA attempt limit/replay guard (H6) | Rotating XFF doesn't reset limits; N failures lock account for backoff window | Limiter tests with spoofed XFF |
| 1.2 | Server-side MFA enforcement (M2) | Un-enrolled user in MFA-required org gets only an enrollment-scoped token | API call with limited token → 403 |
| 1.3 | Media MIME allowlist + safe serving (H4) | html/svg rejected or served as `attachment` `octet-stream`, `nosniff`, sandbox CSP | Upload/serve tests per type |
| 1.4 | Escape template variables, sanitize `body_html`, sandbox print iframes, permission-gate templates (H5), escape `printWO`, portal (M8), QBO callback (M7), reportlab (M10) | XSS payloads render inert | Backend render tests with payloads; vitest for iframe `sandbox` |
| 1.5 | SSRF guard on webhooks (H7) | Private/link-local/loopback URLs rejected on save and delivery; no redirects | Unit tests with mocked DNS |
| 1.6 | Staging = production rules (M5), require `TOKEN_ENCRYPTION_KEY` + separate keys (M6) | App refuses to boot in staging with placeholders | Config validator tests |

### Phase 2 — Tenant isolation & RBAC (~1 week)

| # | Task | Acceptance | Tests |
|---|---|---|---|
| 2.1 | `ensure_owned()` for every body `*_id`; org filter in `post_contact_ledger` & other id lookups (M1) | Foreign ids → 404 across orders, invoices, tasks, v1, inbox, telephony, assets, appointments, returns, stock_docs | Extend `test_idor_tenant_isolation` with two-org fixtures per endpoint |
| 2.2 | Enforce the permission catalog (H3); add enforced codes to catalog | Every non-GET route has `require_perm` or explicit allow-list entry | **Route-gate meta-test** that fails CI on new ungated writes; role matrix tests |
| 2.3 | Record-scope loaders for orders/clients (M3) | "Own only" users can't read/modify others' orders/clients via any route | Scope tests on the 27 routes |
| 2.4 | Gate custom reports, report exports, staff list salary fields, webhook secrets (M9, H3 reads) | Restricted roles get 403 / redacted fields | Permission tests |

### Phase 3 — Money integrity (~1–1.5 weeks)

| # | Task | Acceptance | Tests |
|---|---|---|---|
| 3.1 | Single "net paid / balance due" function used by pay links, tech, engage, refunds, archive (B1, B3, B11, B14) | All paths agree; refunds capped; refund-then-archive refunds once | Scenario tests: in-store pay → link; repeated refunds; archive after refund |
| 3.2 | Currency-correct pay links (B2, B20) | Link currency = doc/org currency; JPY etc. correct minor units | Per-currency tests |
| 3.3 | Input validation on lines, POS, invoices, returns, write-offs (B4, B5, B7, B15) | Negative/over-range values → 422; discount approval & margin guard apply to fixed discounts and revenue ≤ 0 | Boundary tests |
| 3.4 | Locking/atomic updates on balances, stock, intents, coupons, loyalty (B12, B18, B19, B21) | No lost updates | Concurrency tests on Postgres (gather N parallel requests) |
| 3.5 | Archive/restore stock correctness (B6), void-leg protection (B13), till close (B10), multi-invoice orders (B8), float compares (B17) | Each scenario in the table produces correct balances | One regression test per bug |
| 3.6 | One invoice-numbering path + unique constraint (B9); one stock-posting engine (B16) | No duplicate numbers; consistent avg cost & batches | Migration checks for existing duplicates **before** adding the constraint (report, don't auto-rewrite, without your OK) |

### Phase 4 — Production platform (~1 week, parallel with 2–3)

| # | Task | Acceptance | Tests |
|---|---|---|---|
| 4.1 | Dockerfile (builds React), process/proxy config, fail boot if `dist` missing in prod (P1, B22) | `docker build && docker run` serves the app against Postgres | CI builds the image and hits `/healthz` |
| 4.2 | Scheduler leader lock + row claiming + per-message commit (P2) | Two workers never double-send | Test with two runner instances on Postgres |
| 4.3 | Transactional outbox for notifications/webhooks (P4) | Rolled-back request sends nothing | Rollback test |
| 4.4 | Lockfile; CI: Postgres required, pip-audit, npm audit, gitleaks, lint routers/services, fix flaky DNS test, restore coverage ≥ 50% (P5, P6, B23) | CI green and reproducible | — |
| 4.5 | Backups + restore drill doc, durable media storage (P3) | Documented, rehearsed restore | Restore drill checklist |
| 4.6 | Fix runtime-DDL gating and DDL try/except migrations (P7, P8) | Upgrade from a `create_all`-stamped Postgres succeeds | Postgres migration test |

### Phase 5 — Performance & observability (~1 week)
F1–F6 (pagination, eager-load trimming, `to_thread`, indexes via `CONCURRENTLY`, Redis rate
limiter, lazy routes); Sentry + OTel; protect `/metrics`; redact tokens in logs; GDPR completeness
(P9). Acceptance: p95 of list endpoints under target with seeded 10k rows; error tracking live.

### Phase 6 — Cleanup & modernisation
HttpOnly cookie sessions + strict CSP; PyJWT; replace `xlsx`; upgrade react-router/vite/vitest;
delete dead legacy code (ROADMAP Phase 0); split `orders.py`/models; then product roadmap.

---

**What I need from you before implementing:** approval to start Phase 0 (and whether this app is
currently deployed anywhere — that decides whether secret rotation is urgent).
