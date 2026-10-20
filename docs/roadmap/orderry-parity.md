# Roadmap: Orderry parity (Aug–Oct 2026 releases)

Status date: 2026-10-20. Integration branch: `roadmap/orderry-parity`, which is `security/phase-4` + `launch/audit-fixes` merged.
Source analysis: Tekly vs Orderry gap review (24 features, 3 bugs).

## Team model

| Role | Who | Owns |
|---|---|---|
| Product manager / integrator | The coordinating session | This roadmap, contracts, merge order, migration chain, final QA sign-off |
| Workstream teams A–F | One session each, own worktree, branch cut from `roadmap/orderry-parity` | Backend + frontend + tests + self-review for their workstream |
| QA (backend) | Each team, then the integrator on the merged branch | pytest for every endpoint and rule, incl. tenant isolation and permission denial |
| QA (frontend) | Each team, then the integrator | Vitest component tests; Playwright e2e for each user flow; browser review on the merged build |

Each team works in this order: read the contract → backend + migration → tests → frontend → tests → run `/code-review` on its own diff and fix the findings → report.

## Ground rules (all teams)

1. **Sandbox only.** Every third-party call uses test/sandbox endpoints.
   - Stripe test keys (`sk_test_`/`rk_test_`) with simulated Terminal readers. Square `SQUARE_ENV=sandbox`. Twilio test credentials or the simulated adapter.
   - Live keys are refused unless `ENVIRONMENT=production` and `ALLOW_LIVE_PAYMENTS=true` / `ALLOW_LIVE_TELEPHONY=true`. Add these to `config.py`; both default to false.
   - Any shop credentials are stored through `services/org_creds.py` (encrypted) and never returned in API responses.
2. **Every inbound webhook verifies its signature**: Stripe-Signature, Square HMAC, X-Twilio-Signature. It is idempotent on the provider event id, and it is tenant-scoped by the account it was registered for, never by a value in the payload.
3. **Migrations:**
   - One alembic revision per team, `down_revision = "ls144r4"`, with id prefix `op<team letter>` (e.g. `opa1`). The integrator re-chains the revisions at merge time.
   - Keep `test_144eo_migrated_schema.py` green: model and migration must match exactly, including indexes and `ondelete`.
4. **Money** uses `Decimal` and `services/money.py`. Never hardcode a currency symbol; use the org currency (`services/currency.py`).
5. **Permissions:**
   - New actions get a permission in `services/permissions.py`, plus a default per built-in role.
   - Each endpoint gets a test proving a role without the permission gets a 403.
   - Each endpoint gets a test proving another org's id gets a 404.
6. **Shared files** (high merge risk) get minimal, additive hunks only:
   - `backend/models/__init__.py`: append new models at the end of the file; add columns next to existing ones.
   - `frontend-react/src/pages/settings/SettingsHub.tsx`: put new settings UI in a **new component file** and add one import + one mount line.
   - `main.py`: one `include_router` line.
   - `documents.py` variable lists: add your entries in one block.
7. **UI quality bar.** Match the existing design system (`DESIGN.md`, Tailwind tokens, `M` icons, drawers).
   - Every list needs empty, loading and error states. Every money field formats in the org currency.
   - Copy must pass `npm run lint:uitext`.
   - Layouts must work at 375px and 1280px.
   - Nothing may be half-wired: if a backend field exists, it is editable in the UI, or it is deliberately API-only and the report says so.
8. **Gates before reporting:**
   - `make ci-backend` (or `ruff` + `alembic upgrade head` on a fresh DB + `pytest -q -n auto`).
   - In `frontend-react`: `npx tsc --noEmit && npm run lint:uitext && NODE_OPTIONS=--no-experimental-webstorage npx vitest run && npm run build`.
   - Commit on your own branch. Do not push, merge, or touch other branches.

## Workstreams, data and contracts

### A. Catalog and public page (Now: bugs + S items)
| Item | Data | API | UI |
|---|---|---|---|
| A1 Service-save data loss (bug) | – | `PUT /catalog/services/{id}` only writes fields in `model_fields_set` | Service drawer sends and edits all fields |
| A2 "Show on online booking" | `Service.bookable` (exists) | existing | Toggle in the service drawer; badge in the services list |
| A3 MPN | `Part.mpn String(64)`, indexed per org; optional uniqueness check on save | Products CRUD, search (`q` matches mpn), import/export column, `/api/v1/products` field, print var `{Product MPN}` | Product drawer field, list column, filter |
| A4 Barcode source per label | `PrintTemplate` option `barcode_field` ∈ barcode/sku/code/mpn, plus a type per field (code128/ean13/upca/qr) | `printing.py` renders the chosen field; ean13/upca validated | Label template editor picker |
| A5 Scheduled date on track page (bug) | existing `scheduled_for/to` | existing | `PublicTrack.tsx` shows "Scheduled for", or Start/End for multi-day |

### B. Billing (Now + Next)
| Item | Data | API | UI |
|---|---|---|---|
| B1 Invoice display mode | `Invoice.display_mode`, org default (exist) | existing | Picker on invoice create/edit + default in Finance settings |
| B2 Record invoice payment | existing payments | `POST /finance/invoices/{id}/pay` (exists) | "Record payment" drawer: amount (partial allowed), account, method, date; Balance due block |
| B3 Overpayment | Client balance (existing store-credit ledger) | `/pay` accepts amount > balance with `overpay=keep_balance\|refund` | Choice shown when the amount exceeds the balance |
| B4 Credit notes | New `CreditNote` (+ lines): number (numbering engine doc_type `credit_note`), client, optional invoice_id, status draft/issued/canceled, reason, totals with tax. Issued = locked. Ledger: issued note linked to an invoice reduces its balance; a standalone one adds client credit; canceling reverses. | `/finance/credit-notes` CRUD + `/issue` + `/cancel`; invoice payload gains `credited` and `balance_due`; v1 read endpoint | Credit notes list + drawer; "Create credit note" from invoice; print template + PDF; shows on client finance tab |

### C. Notifications and chat inbox (Now + Next)
| Item | Data | API | UI |
|---|---|---|---|
| C1 `{total}` currency (bug) | – | Formatted with org currency, decimals kept; sweep user-facing ₹ literals in backend/services and notification paths | – |
| C2 Template variables | – | Add `{client_first_name} {client_last_name} {client_phone} {portal_link} {balance_due} {scheduled_for} {client_birthday} {client_age}`; missing value → omitted cleanly | Variable picker in template editor lists them |
| C3 Chat statuses | `ChatThread.status` new/reply_needed/replied/completed, `last_inbound_at`, `last_outbound_at`, `is_spam`, `spam_at` | Status auto-updates on message in/out; replied → completed after 24h (scheduled job in `core/scheduled.py`); a new client message reopens; `PATCH /chats/{id}` status/spam; list filters | Status tabs with counts in ChatsHub; Spam folder; mark as spam / restore; muted spam (no notifications) |
| C4 Retire dead inbox | Decide: fold `InboxThread` webhooks into `ChatThread`, or delete the unused router. Document the choice. | – | – |

### D. Telephony on Twilio sandbox (Integration)
| Item | Data | API | UI |
|---|---|---|---|
| D1 Twilio voice adapter | Org creds via org_creds; `Call` gets provider_sid and direction | Click-to-call via Twilio REST (test creds); `POST /comms/twilio/voice` + status callback, signature-verified, idempotent | Settings card (new component) with "Test connection" |
| D2 Auto-create contacts from calls | Automation settings: enabled, direction incoming/all, tags[], assign manager | On an unknown number: create Client (name = formatted phone, source "call"), apply tags, assign manager (employee who placed the call, or extension owner) | Settings in the telephony card; contact shows "Created from call" |
| D3 Tag filter on contacts | – | `GET /clients?tag=` | Tag filter chip in the Clients list |

### E. People and workflow (Next)
| Item | Data | API | UI |
|---|---|---|---|
| E1 My earnings | – | `GET /payroll/me?from&to` (own only, new perm `can_view_own_payroll`, default on for every role) with commissions broken down to rule → order/doc → item | "My earnings" page (avatar menu + Finance) |
| E2 Payroll report filters + drill-down | – | Payroll calc/report accept location_id, position/role, employee_id; breakdown returns rule name, document, line item | Filters on the payroll screen; expandable rows; export keeps filters |
| E3 Payroll scope | Permission `payroll_scope` = own/all/selected employee ids | Enforced on every payroll endpoint | Role editor control |
| E4 Checklist auto-attach | `ChecklistTemplate.auto_status_keys[]` (record type aware) | On a status change into the key, attach the template once per order (idempotent) | Template editor "Attach automatically when order enters…" multi-select |

### F. POS and card-present payments (Next + Integration)
| Item | Data | API | UI |
|---|---|---|---|
| F1 Scan-to-cart | – | existing lookup by sku/barcode/code/mpn | Keyboard-wedge (fast keystrokes + Enter) and camera ScanButton add to cart; exact match adds, otherwise searches; beep/flash feedback |
| F2 Stripe Terminal (server-driven, simulated reader) | `PaymentTerminal` (provider, reader id, label, location), `Payment.provider_ref` | Register reader (test `simulated-wpe`), `POST /payments/terminal/charge` (process_payment_intent), `payment_intent.succeeded/failed` webhook posts the payment, cancel, refund; test-helper endpoint to "present card" in non-prod | "Charge on reader" in POS + order payment drawer with live status (waiting → approved/declined), cancel |
| F3 Square Terminal (sandbox) | same model, provider=square | Terminal checkout API with sandbox test device ids; webhook `terminal.checkout.updated` | Same UI; provider chosen per terminal |
| F4 Guardrails | `ALLOW_LIVE_PAYMENTS` | Live keys refused outside production | Settings shows "Test mode" badge |

Out of this round:
- **Tap to Pay on the phone itself.** It needs a native wrapper (Capacitor + Stripe Terminal/Square Mobile SDK); plan it with Tekly Go native.
- **Not doing for now:** Shopify/WooCommerce tax & photo sync, bundle images, per-contact currency ledgers, resource auto-assignment, schedule/timesheet API endpoints, transfer shipments, delivery address.

## Now / Next / Later

| Horizon | Items | Status |
|---|---|---|
| **Now (pre-launch)** | A1 A2 A5 B1 B2 C1 C2 | Not started |
| **Next (launch +0–4 wks)** | A3 A4 B3 B4 C3 C4 E1–E4 F1 | Not started |
| **Integration (sandbox; launch decision later)** | D1–D3 F2–F4 | Not started; go-live needs live creds, a provider account review, and a legal check on call recording/consent |
| **Later** | Tap to Pay native, multilingual notifications, delivery address + shipments, real e-commerce sync | Not planned |

## Dependencies and risks

- **Shared files.** models, SettingsHub and documents.py will all be touched by several teams. This is mitigated by the additive-hunk rule and the integrator merging in order A → C → B → E → F → D.
- **Migrations.** Six revisions off one head. The integrator re-chains them; each must be replayable on a fresh DB and on Postgres.
- **B4 needs** the numbering engine (`services/numbering.py`) and the ledger (`services/ledger.py`); B3 reuses B4's client-credit posting. Same team, so no cross-team wait.
- **D2 needs D1** (same team). C3 and D both add webhooks under `comms` routers; C owns `chats.py`, D owns `telephony.py` and the call webhook in `comms.py`.
- **F2/F3 cannot be truly end-to-end without provider test accounts.** Teams build against the providers' documented sandbox behaviour with HTTP mocked in tests (respx). With test keys present in env, a live sandbox smoke test runs; without them it is skipped and reported.
- **Go app branches (PRs #8–#11) are not merged into phase-4**, so Go-side screens (earnings, quick sale) are deferred until those land.

## Integration and QA plan (integrator)
1. Merge team branches in order and re-chain migrations. After each merge, run the full gates.
2. Run `alembic upgrade head` from scratch on SQLite, and on Postgres if available.
3. Browser review on a seeded local build, at 1280px and 375px. Walk through:
   - edit a service and confirm nothing is lost;
   - the booking page;
   - an MPN scan in the POS;
   - partial payment → overpay → credit note → print;
   - chat statuses and spam;
   - a simulated call creates a contact;
   - my earnings;
   - checklist auto-attach;
   - a simulated card reader charge.
4. Do a final `/code-review` of the merged diff, fix the findings, and write the sign-off report.
