# Full codebase audit — 29 Oct 2026

**Scope:** branch `claude/orderry-ws-a-stock`, i.e. `roadmap/orderry-parity` with PR #21 merged in (head e906cda). Backend, frontend, CI, Docker and config.

**How:** Strix could not run here: it needs Docker, the Strix CLI and its own paid LLM key, and none of these are installed. Instead the audit used:
- **Automated scanners:** pip-audit, npm audit, bandit, semgrep (python, react, secrets and OWASP rule packs) and detect-secrets.
- **Six code-reading reviews:**
  - sign-in and sessions
  - tenant isolation / IDOR
  - injection, files, SSRF and XSS
  - payments and money logic
  - config, deploy and privacy
  - reliability and performance

Every High finding below was re-checked by hand in the code. Nothing was executed against a live server, so these are traced exploits, not proven ones.

## Scanner results
| Tool | Result |
|---|---|
| pip-audit (locked deps) | **Clean** |
| npm audit, production deps | **Clean** (0) |
| npm audit, dev deps | 10 (3 critical, 3 high) in vitest / vite / undici. Development tooling only; not shipped to browsers |
| bandit | 98 results, none High. The 8 "SQL string" hits are in one-off maintenance scripts with fixed table lists. 48 are `try/except: pass` (see R1) |
| semgrep | 38 results: 26 workflow actions pinned by tag rather than commit, 9 `text()` calls (all bound parameters or migrations), 2 in the legacy `frontend/static` app, and 1 editor `innerHTML` (content is cleaned with nh3 on save) |
| detect-secrets | No real secrets. All hits are test passwords, migration revision ids, placeholder `.env.example` values or the dev `docker-compose` password |
| Backend tests | 1961 passed (last full run). Ruff and tsc clean. Vitest 309 passed |

## Findings, most severe first

### High — fix before go-live
| # | Area | Where | What can happen | Fix |
|---|---|---|---|---|
| H1 | Tenant isolation | `routers/returns.py:48-62` | Shop B sends a client return with shop A's product id and adds 10,000 units to A's stock. On-hand totals sum across all locations by product id with no shop filter, so A's stock, reorder and valuation figures change. **Verified.** | Check `part_id` belongs to the shop (as `create_po` does) |
| H2 | Tenant isolation | `orders.py:3448` add line → `orders.py:2486` create-po → `inventory.py:1814` receive / `:1695` recost | Shop B puts A's product on its own job, raises a PO from it and receives it. A's average cost and stock change | Check `ref_id` ownership on product and service lines; scope the `Part` loads in receive and recost |
| H3 | Tenant isolation | `stock_docs.py:229-233, 396-400` (+ reverts `:291, :497`) | Shop B writes off or returns shop A's serial unit by id, flipping it to written-off and back | Filter `SerializedItem` by shop and part; validate `part_id` |
| H4 | Privilege escalation | `staff.py:173-188, 326` | A manager whose role is limited to some locations, their own orders or their own pay sets their **own role to "none"**. "No role" is treated as unlimited, so they get every location and every record, and can change their own pay. The same works through an invite to a second email. **Verified.** | Only the owner may give "no role"; better, every non-owner must have a role |
| H5 | Money | `engage.py:55-60`, `orders.py:3312-3325`, `order_money.py:64` | Discount lines (gift card, coupon, loyalty) plus deleting a line push the order total below 0. That reads as "overpaid", and the refund endpoint pays it out as cash. Concurrent redemptions also over-discount (no row lock). **Verified: there is no floor at 0.** | Floor the total at 0; lock the order row in the redeem endpoints; re-cap discounts when lines change |

### Medium
| # | Area | Where | Issue | Fix |
|---|---|---|---|---|
| M1 | XSS | `public/PublicTrack.tsx:506`, `routers/feed.py:29,148` | A file item's `media_data` is stored unchecked and rendered as `href` on the **public tracking page**. A `javascript:` URL runs when the customer (or a signed-in owner) clicks Download. **Verified** | Accept only `data:<allowed mime>;base64,` on the server; only render `data:` or `/api/media/` links |
| M2 | Tenant isolation | `transfers.py:157, ~222` | Transfer lines accept another shop's product; error messages leak its name | Same ownership check |
| M3 | Money | `cash.py:403-431` | Restoring a voided sale or refund transaction puts the money back in the till while the document stays void | Apply the same guards in restore that delete has |
| M4 | Money / stock races | `cash.py:366/404`, `transfers.py:170`, `stock_docs.py:269/477/707` | Two identical delete, restore, complete or cancel requests sent at the same moment apply twice | `with_for_update()` or a conditional UPDATE with a rowcount check |
| M5 | Config | `config.py:37, 185, 229` | If `ENVIRONMENT` isn't set on a server, it runs as development: the public default `SECRET_KEY` (anyone can forge sign-ins), and simulated pay links that mark orders paid | Fail closed unless `ENVIRONMENT` is explicitly development or test |
| M6 | Platform admin | `services/platform_auth.py:27-51` | The super-admin account has no 2-step sign-in, tokens can't be revoked, and response time reveals which emails exist | TOTP, token version, constant-time login |
| M7 | Privacy | `db/database.py:34`, `core/logging.py:59` | Database error tracebacks print query parameters (emails, phones, hashes) into logs and Sentry | `hide_parameters=True`; redact exception text |
| M8 | Secrets at rest | `routers/mfa.py:37` | 2-step (TOTP) secrets are stored in plain text, so a leaked backup defeats 2-step | Encrypt with `services/crypto` |
| M9 | Audit trail | `integrations_credentials.py`, `webhooks.py`, `sso.py`, `admin.py` | Changing Stripe/Twilio keys, webhooks or SSO, and platform-admin actions, leave no audit record | Record who and what changed (never the values) |
| M10 | Credentials | `services/org_creds.py:108-183` | A failed decrypt silently falls back to the operator's own payment keys | Fail closed; re-encrypt and drop the legacy key |
| M11 | SSRF | `core/ssrf.py:35`, `services/automation.py:123` | 100.64.0.0/10, which includes a cloud metadata IP, counts as public. The automation webhook can be hit by DNS rebinding | Use `addr.is_global`; pin the resolved address as webhooks already do |
| R1 | Reliability | `orders.py:1008-1069` and 46 bare `except: pass` | Order side effects (commissions, draft invoice, alerts) fail silently and can leave the session broken | Log and roll back, or use a savepoint per hook |
| R2 | Reliability | `core/scheduled.py:55-62` | One shop's error in the notification sweep stops every later shop that tick | Roll back per shop |
| R3 | Reliability | `services/webhooks.py:125`, `scheduled.py:104` | A crash mid-send leaves a webhook stuck at "sending" forever; no backoff | Reclaim stale sends; add a next-attempt time |
| R4 | Money accuracy | `models/__init__.py` | 87 of 112 money columns return floats; several money fields are plain `Float` | Carry out `SPEC_money_decimal.md` |

### Low (batch these)
- Customer portal links never expire and are stored in plain text (`clients.py:271`).
- Small leaks that show whether an email has an account: register, forgot-password timing, and the suspended message.
- A reset token can be redeemed twice by two simultaneous requests.
- API keys outlive the person who created them.
- A payment link with an explicit currency skips the "no more than owed" cap (`payments.py:76`).
- An unknown cash account id is accepted silently (`cash.py:76`).
- Client and product CSV exports made in the browser skip formula-injection protection (`ClientsHub.tsx:139`, `InventoryHub.tsx:285`).
- Client data isn't HTML-escaped in HTML emails (`message_vars.py:58`). Strip newlines from email subjects.
- LIKE wildcards aren't escaped in catalog, leads and custom-report search. A brand named `%` matches every brand.
- Workflows have no `permissions:` block and actions are pinned by tag rather than commit.
- Rate limits are counted per worker when Redis is missing. One edge case trusts the client's IP header.
- The dev `docker-compose` exposes Postgres and pgAdmin on all network interfaces.
- `/api/health` shows the environment name. The CSP has no `form-action`.
- No data-retention purge jobs.
- Device passcodes from industry forms may sit in plain JSON. **Needs check.**

### Quality and performance
- **N+1 queries:** 48 list endpoints run a query per row. The worst, `returns.py:161`, runs about 600 queries per page.
- **Whole-table loads:** 14 places load a shop's entire product table just to look up a few names.
- **Missing indexes:** 220 of 450 foreign keys have none, including the hot ones `POLine.part_id`, `StockReservation.part_id` and `Task.order_id`.
- **Two posting engines:** `stock_docs` postings is unused by the UI and duplicates `stock_posting`.
- **Timestamps:** 211 `utcnow()` calls and 0 timezone-aware columns; "today" uses the server's timezone, not the shop's.
- **Frontend:**
  - 94 `.catch(() => {})` calls hide errors, and there's no error boundary, so one crash blanks the app.
  - The 4 MB icon font is the largest download.
  - 256 clickable `div`/`td` elements can't be reached by keyboard.
- **Test coverage:** backend 50% of lines (20% of branches); frontend 2.8% of lines.

## What is solid (verified)
- **Sign-in and passwords:**
  - JWT handling: pinned algorithm, revocation and token versions.
  - Passwords and reset tokens: bcrypt, 256-bit hashed single-use tokens.
  - 2-step sign-in: replay guard and capped attempts.
  - Rate limits on every sign-in path; API keys stored hashed.
- **Data isolation:** owner protections and role-widening checks; a flush-time tenant guard on rows that have `org_id`.
- **Payments and money:**
  - Stripe and Square webhooks are signed, timestamped and idempotent.
  - Live-payment guard.
  - Pay order, refund, credit note and payroll paths are locked and capped.
- **Injection and files:**
  - No SQL injection, deserialisation or command execution.
  - Uploads are magic-byte checked, SVG and HTML are served as attachments, and the zip-bomb guard works.
  - Documents are sanitised with nh3 and escaped.
  - Tenant webhooks are pinned against SSRF.
- **Deploy and config:**
  - Deployed config fails closed (secret key, CORS, SQLite, https, Fernet).
  - Strict CSP with no inline script; HSTS.
  - Non-root Docker image with hash-pinned dependencies.
  - No secrets committed; gitleaks runs in CI.
- **Database:** a single Alembic head and a model↔migration drift gate.

## Suggested order
1. **Now:** H1–H5 plus M1 (one PR: tenant guard for line tables and `ref_id`, the role-"none" rule, the order-total floor and locks, the safe file link).
2. **Next:** M2–M11 (locks, restore guards, config fail-closed, platform-admin 2-step, log redaction, TOTP encryption, audit trail, SSRF).
3. **Then:** R1–R4 and the Low list; the quality items as ongoing clean-up.
4. **Strix:** to get exploit-proven results on top of this, install Docker, then `pipx install strix-agent`, set `STRIX_LLM` and `LLM_API_KEY`, and run `strix -n -t ./ --scan-mode standard` against a local dev server, never production. Or use app.strix.ai.
