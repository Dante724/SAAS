# Council note — Workstream C: inquiries everywhere (24 Oct 2026)

Sprint plan: `docs/plans/2026-10-24-orderry-jun-jul-sprint.md` (main checkout). Gap analysis items #8–#12, #17, #18, #20.
Base: `roadmap/orderry-parity`. Owns leads/inquiries, telephony, Tekly Go inquiries, public API, webhooks, contact addresses and three settings. Does not touch inventory, postings, POs, assets, reports, profit or invoice templates.

## PM: what the shop gets
1. **A missed call becomes an inquiry.** When a call to the shop's Twilio number goes unanswered, Tekly opens an inquiry, links it to the contact if the number is known, and creates a "Call back" task. It also alerts everyone who is clocked in right now (or the admins if nobody is). If the same person calls again within 24 hours, the call is added to the open inquiry ("3 missed calls"), so there are no duplicates. This is on by default and can be turned off in Calls & telephony.
2. **Richer inquiries on the web.** Each inquiry has a type (with its own respond-by time), the device (brand, model and serial/IMEI), the shop's own custom fields, and an activity timeline (comments plus automatic entries). Converting one creates the job with the device attached.
3. **Inquiries in Tekly Go.** The backend gives Go a paged list, a create that is safe to retry offline (idempotency key) and convert-to-job. The Go screen is built on PR #18's stack, which isn't in the base yet; see below.
4. **API v2 and webhooks.** `/api/v2` adds inquiries, tags, services, bundles and writable products. Keys can now be limited to certain resources (for example `inquiries:write`) and are rate-limited per key. The webhook events now match the docs: `order.closed` and `estimate.accepted` actually fire, and the docs no longer promise `booking.created` (the old name still works as an alias for `appointment.created`).
5. **Three half-built settings switched on.** Default visibility for comments and files, how staff names show on public pages, and auto-printing the asset label when a job is created.
6. **Addresses in fields.** Contacts get street, city, region, postcode and country. The single address line is still filled in, so old documents and exports don't change.
7. **Aircall adapter.** Off unless the server switch `AIRCALL_ENABLED=true` is set *and* the shop saves its Aircall webhook token.

Out of scope: inventory, reports, invoice templates (A and B own them).

## Engineer: how
- **One migration** `wsc1` after `opd1`. It adds the `lead_types` table; on `leads` it adds type, device, serial, custom fields, call count, last call time, an idempotency key (unique per org) and updated time. It also adds the structured address fields on `clients`, `organisations.public_employee_name`, and `api_keys.rate_limit_per_min`. Nothing is dropped or rewritten, and old address lines are left as they are.
- **`services/leads.py`** is the single place for creating, updating and converting an inquiry and for handling a missed call. The web router, API v2 and Go all call it.
- **Missed calls:** `comms.record_inbound_call` and the Twilio status webhook already find the move to "missed". Both now call `leads.on_missed_call()`. The older `task_on_missed_call` automation only runs when the inquiry flow didn't already create a call-back task, so there are never two tasks.
- **On duty** means an open attendance `Shift` at one of the shop's locations, for active employees. If nobody is on duty, the alert goes to the contact's manager, then to the admins (`can_manage_settings`).
- **Activity timeline:** reuses `EventFeedItem` with `entity_type="lead"` (public.py already writes lead events there), so comments, files and visibility behave the same as on jobs.
- **Custom fields:** reuses `CustomField` with `entity_type="lead"` and `forms.resolve_custom_data` (required and locked rules).
- **API v2** is a new router file. It shares v1's serializers, and every referenced id (client, location, employee, tag, service) is checked against the key's shop. Scopes: the legacy `read`/`write`/`*` keep working, and new `<resource>:read|write` scopes narrow a key. The rate limit runs in `get_api_principal` (it also covers v1): 120 requests a minute by default, or the key's own limit.
- **Aircall:** `services/aircall.py`. Aircall authenticates with a shared `token` in the payload (no HMAC). The token is compared in constant time with the per-shop token, stored encrypted in `org_creds`. Calls are idempotent on `provider_sid="aircall:<id>"`. Unhandled events get a 200, because Aircall disables a webhook after repeated failures.

## Tester: what proves it
- Missed call by Twilio (signed): an inquiry is created, the contact matched and a task created. A second call within the window merges (one inquiry, count 2, one open task); a call outside the window creates a new inquiry. The legacy task isn't duplicated. An unsigned or wrong-shop webhook creates nothing. On-duty staff get the alert; with nobody on duty, it falls back.
- Inquiries: type sets the respond-by time; required custom field gives 422; a manager or type from another shop gives 404; convert creates the job plus the device; converting twice gives 409; an idempotent create returns the same row; the timeline shows the entries.
- API v2: scope matrix (a narrow key gets 403 on other resources); a cross-tenant id gives 404; rate limit gives 429; product writes; webhook events fire for inquiry, tag and bundle.
- Settings: an org default of public makes a comment client-visible unless it is overridden; the booking staff list follows the name setting; the auto-print endpoint lists templates set to print on create.
- Addresses: setting the fields fills the line; the old line survives the migration; the v1/v2 contact shows both.
- Aircall: sample payloads from the docs; a bad token gives 403; server switch off gives 404; a missed call (`answered_at` null) gives an inquiry; replays are idempotent.
- Frontend: vitest for the inquiry drawer (type/device fields, timeline, convert) and the address fields.

## Security
- **Webhook authenticity:** Twilio stays HMAC-SHA1 with the shop's own token plus an AccountSid and number match. Aircall uses a constant-time token compare with the per-shop token, never one shared operator token. The tenant always comes from the verified credential, never from the payload.
- **Tenant isolation:** every foreign id in lead, API and settings writes is checked against `org_id`. This includes the old `task_on_missed_call.assign_to` and the lead `manager_id`, which weren't checked before.
- **No PII in logs:** push and log titles stay generic ("Missed call — call back needed"); names and numbers go only in the notification body stored for staff. No phone numbers in log lines.
- **API keys:** rate-limited per key; a narrow scope can't escalate; webhook secrets are returned only to `webhooks:write` keys; endpoint URLs are SSRF-checked as before.
- **Settings values** are validated against allow-lists (visibility, name mode).
- **Public pages** never get an employee's full name when the shop picks first name or initials.

## Outcome (25 Oct 2026)
- **Security review findings, all fixed:**
  - Broad keys no longer imply webhook management.
  - Converting an inquiry through the API needs orders and contacts write.
  - `client_id` is hidden from keys without contacts read.
  - Staff convert needs `can_create_tickets`.
  - Generic call-webhook replays are deduped.
  - Bursts of repeat calls send one alert.
  - Aircall ids are validated, the token minimum is 16, and failed token attempts are rate-limited per shop.
  - The idempotency fallback no longer turns a non-key error into a 500.
- **Tester review findings, all fixed:**
  - A call reported ringing then missed now raises one inquiry.
  - Convert reuses only the same customer's device.
  - Converted inquiries can't be reopened, and the call-back task closes on convert.
  - The address line and its parts stay in step.
  - Changing the type moves the respond-by time, and archived types are refused.
  - Estimates no longer emit `order.created`.
  - v2 paging has a stable order.
  - `estimate.accepted` now has a behavioural test.
- **Kept as is (owner to confirm):**
  - Missed call → inquiry is on by default for every shop.
  - A comment or file posted without a visibility uses the shop default.
- **Tekly Go inquiries screen:** blocked on PR #18, the Go PWA stack, which isn't in `roadmap/orderry-parity`. The backend Go needs is in this PR: the paged `GET /api/leads`, an idempotent `POST /api/leads` and `POST /api/leads/{id}/convert`. The screen is a follow-up stacked on #18.
