# Self check-in — Product & Build Spec

*Status: design, pre-build. This spec is the output of a competitive review against BenchKey and a brainstorm with the owner.*
*Target branch: `release/1.0`. The spec is meant to be implementable as a single PR.*

---

## 1. Summary

A customer walks up to a shop, scans a QR code and checks themselves in on their own phone, or on a tablet at the counter. They:

- pick what they need (a job type);
- scan or type their vehicle, vessel or device identifier;
- answer the shop's own questions;
- sign the shop's terms.

Then they get a place in the queue, with a text message and a live status page.

On the shop side, staff see a **Walk-ins** queue inside **Inquiries**. They tap **Call next**, and the check-in turns into a **fully filled work order**, which prints automatically if the shop has set that up. After hours, the same QR switches to a **key drop** that feeds an overnight list.

The design is driven by three decisions taken with the owner:

1. **The QR is unique per check-in point, and a location can have several points**: front door, key box, dock, counter tablet. There is never one shared code per tenant, and nothing is global.
2. **The form is customisable, but it doesn't add a second form builder.** It is assembled from the shop's industry template, the job types the point offers, and those job types' custom fields that are ticked *Ask at check-in*.
3. **Requests go into the existing Inquiries menu.** There is no new top-level menu.

## 2. Goals and non-goals

**Goals**
- Intake without staff. The customer enters their own details, device and complaint, and signs the terms while the tech keeps working.
- Zero setup to start. Every location gets a working "Front door" point and form on day one.
- Converting a check-in fills in everything on the work order, with nothing re-typed.
- Strict tenant and location isolation on a public, unauthenticated surface.

**Non-goals for this PR** (parked):
- payment at check-in;
- membership benefits shown at check-in (the memberships spec will add them);
- multi-language forms;
- per-point analytics beyond scan and submit counts;
- appointment-slot booking from the check-in page. Online booking stays where it is.

## 3. Who uses it, and what they're trying to do

| Who | When | What they want |
|---|---|---|
| Walk-in customer (tech / electronics) | Standing at the counter while staff are busy | "Get me in line without waiting to be noticed, and tell me how long." |
| Auto customer | Dropping a car off before work, often before opening time | "Leave the keys and the details, and go." |
| Marine customer | At the yard or dock | "Log my boat, its engine hours and the problem, and let the yard call me." |
| Counter staff / service advisor | Mid-job | "Show me who's waiting and in what order, then give me a job I don't have to type." |
| Owner | Setup | "Make the form ask *our* questions, with a different QR per door or location." |

## 4. Concepts

### 4.1 Check-in point
A check-in point is a physical place where people arrive. Each point belongs to exactly one location, and a location can have **several**.

| Field | Meaning |
|---|---|
| name | Shown to staff and on the poster: "Front door", "Key box", "Dock 3" |
| mode | `walk_in` · `key_drop` · `auto` (walk-in during working hours, key drop outside them) |
| kiosk | The page runs on a shared tablet. It goes full screen and clears itself 30 s after each check-in. |
| job_type_ids | Which job types the point offers. If empty, the shop's default job type is used. |
| welcome_text, terms_template_id, require_photo, show_wait | Optional per-point overrides; otherwise the shop default applies |
| token | Unguessable public code (≥ 128 bits, URL-safe). Encoded in the QR. Can be rotated. |
| active, scans, submits, rotated_at | State and simple counts |

When a location is created (and in a backfill for existing locations), it gets one point: `Front door`, mode `auto`, offering the default job type.

### 4.2 Check-in
A check-in is one customer's visit, created from a point. It is stored as an **Inquiry (`Lead`) with `source="walk_in"`**, so the inquiries board, the response deadline (SLA), search, reports, webhooks (`inquiry.created`) and **Convert** all keep working. Check-in-specific data lives in a side table (§5.2) so the Lead model isn't overloaded.

### 4.3 How the form is built (no second form builder)
A check-in form is assembled at request time from four sources:

1. **Industry template** (`services/industry.py`): the asset label (Vehicle / Vessel / Device), the asset groups (Car · Truck · SUV · Van / Boat · Jet ski · Outboard · Trailer / Phone · Tablet…), the identifier rule per group (VIN, HIN, engine serial, IMEI/serial), and the template's standard fields (mileage, hours, plate, registration, length…).
2. **Job types the point offers.** The customer picks one ("What do you need?"). If there is only one, the step is skipped.
3. **That job type's custom fields with `ask_at_checkin = true`** (`CustomField`, entity `order` or `device`). Their existing `required`, `placeholder`, `options`, `show_when_*` and `field_type` all apply.
4. **The point's overrides**: welcome text, terms, required photo, wait display.

Rules:
- **Only customer-facing fields.** A field reaches the public page only if it is explicitly ticked *Ask at check-in*. Template standard fields come from a short allow-list: identifier, make/brand, model, year, plate/registration, mileage/hours, colour. Internal fields (cost, tech notes, `watch_out`) are never exposed.
- **Sensitive fields** (`password` type, e.g. passcode) are optional, masked as the customer types, stored encrypted the same way `Device.passcode_enc` is, and never shown on the status page.
- **A copy of the form is kept with each check-in.** The resolved form definition is saved on the check-in, so a job type edited later never changes what the customer saw and answered.

Because each location (or each point) chooses its own job types, **one business doing marine, auto and electronics** just offers different job types at different points or locations. The industry template itself doesn't change.

## 5. Data model

One Alembic migration, chained onto the current head so there is still a **single head**.

### 5.1 `checkin_points`
`id, org_id (fk, idx), location_id (fk, idx), name, mode, kiosk (bool), job_type_ids (json list), welcome_text, terms_template_id (fk DocumentTemplate, null), require_photo (bool), show_wait (bool, default true), token (unique idx), active (bool), scans (int), submits (int), rotated_at, created_at`.

Unique constraint: `token`. Index: `(org_id, location_id)`.

### 5.2 `checkins`
`id, org_id (fk, idx), location_id (fk), point_id (fk), lead_id (fk, unique), kind ('walk_in'|'key_drop'), queue_no (int), queue_day (date, shop-local), state ('waiting'|'called'|'serving'|'no_show'|'converted'|'cancelled'), job_type_id (fk, null), asset_group, identifier_kind, identifier_value, decoded (json: make/model/year/source), reading_kind ('mileage'|'hours'|null), reading_value (numeric), answers (json: field_key → value), form_snapshot (json), signature_png, signer_name, signer_ip, terms_hash, photo_ids (json), key_tag, status_token (unique idx), called_at, converted_order_id (fk, null), created_at, updated_at`.

- Unique constraint `(org_id, location_id, queue_day, queue_no)`. The queue number is taken inside the insert transaction (`max+1` with a retry on conflict), so two people checking in at once can't get the same number.
- `status_token` is a separate unguessable code, used by the customer's status page and the counter QR (§7.4). It is different from the point token.

### 5.3 `custom_fields.ask_at_checkin`
New boolean, default `false`, next to `show_in_portal`. It is exposed in Settings → Forms like the other flags.

### 5.4 No changes to `Lead`, `Order` or `Device` columns
- The reading goes into `Order.custom_data[<template field key>]` (e.g. `mileage`, `hours`), and the latest value is also written to `Device.custom_data`, the same keys the asset form already uses.
- The signature becomes a `DocumentSignature` row with `doc_type="check_in"` when the check-in is converted.
- Photos go to `Order.intake_photos`, the existing field that keeps untouched check-in shots for disputes.

## 6. Customer flow (public)

All public routes live in `routers/public.py` (or a new `routers/checkin_public.py` mounted under `/api/public`). They take a token, never an org or location id.

| Step | Route | Notes |
|---|---|---|
| Open | `GET /api/public/checkin/{token}` | Returns: shop name, logo and brand colour; point name; the effective mode (key drop if `auto` and outside `Location.working_hours`, in `Location.timezone`); the resolved form; whether wait is shown and the current waiting count. Increments `scans`. Inactive or rotated token → `410` with "This code isn't used any more. Please ask at the counter." |
| Scan or type identifier | `POST /api/public/checkin/{token}/identify` | Public, rate-limited wrapper over the existing identify logic: normalise, check digit (Luhn for IMEI, VIN check digit), VIN decode via NHTSA vPIC (best effort), HIN decode. Returns **only** the decoded make/model/year and a `problem` hint. It **never returns matches, owners or history** to the public. Matching against existing devices and clients happens on the server at submit time and stays internal. |
| Submit | `POST /api/public/checkin/{token}` | Body: name, phone (required), email (optional), job_type_id, asset_group, identifier, reading, answers, signature, photos (key drop), key_tag (key drop). Validated against the **server-side** resolved form, so required fields are enforced and unknown fields are dropped. Creates the Lead and the Checkin in one transaction and takes the queue number. Sends the SMS. Returns `status_token`, `queue_no` and the position. |
| Status page | `GET /api/public/checkin/s/{status_token}` | Shows first name, queue number, people ahead, an estimate if allowed, a short summary (asset label and complaint), and the counter QR (§7.4). Polled every 20 s. After conversion it shows "You're being looked after" and the job's tracking link. |
| Cancel | `POST /api/public/checkin/s/{status_token}/cancel` | Customer leaves. The state becomes `cancelled`. |

**Frontend**: `src/public/PublicCheckin.tsx`, at the public route `/c/:token`, plus `PublicCheckinStatus.tsx` at `/c/s/:statusToken`. It reuses `ScanButton` (camera, every barcode type), the signature pad already used on the public track page, the industry field renderer and `CustomFields`. It is mobile first. Kiosk mode hides navigation and resets after submit.

**Estimated wait** = people ahead × the location's rolling median of "check-in → called" over the last 14 days. If there is too little history, it falls back to 10 min per person. It is shown as "about N min", rounded to 5, and hidden when `show_wait` is false.

## 7. Staff flow

### 7.1 Inquiries → Walk-ins view
This is a third view next to **Pipeline** and **List** in `pages/inbox/InquiriesHub.tsx`, scoped to the user's location (with a switcher for multi-location roles).

- **Waiting**, in queue order. Each row shows the number, name, asset summary with the reading, job type, time waiting and a "Seen before" chip. Buttons: **Call next**, **No-show**, open the drawer.
- **Being served**: called but not yet converted.
- **Overnight drop-offs** (`kind=key_drop`, not yet converted). Each shows the key tag and a photo thumbnail, with a **Start job** button.
- The header shows the waiting count and a **Print door QR** link to Settings.

A **sidebar badge** on Inquiries shows the number of waiting check-ins for the user's location. It comes from a cheap count endpoint, polled every 30 s and refreshed by the existing feed events.

### 7.2 Actions (signed-in staff, the same as inquiries today; Convert also needs `can_create_tickets`, as `POST /api/leads/{id}/convert` does)
| Action | Route | Effect |
|---|---|---|
| Call next | `POST /api/checkins/{id}/call` | state → `called`, SMS "You're up, please come to the counter". Optional `convert=true` converts in the same call. |
| No-show | `POST /api/checkins/{id}/no-show` | state → `no_show`; the Lead is marked lost with reason "No-show". |
| Convert / Start job | `POST /api/checkins/{id}/convert` | See §7.3. Returns `order_id`; the frontend then runs `autoPrintForJob(order_id, "create")` (from PR #41). |
| Lookup by code | `GET /api/checkins/by-code/{status_token}` | Used by the counter scan (§7.4). |
| List / count | `GET /api/checkins?location_id&day` · `GET /api/checkins/count` | Feeds the Walk-ins view and the badge. |

### 7.3 Convert: what lands on the work order
This extends `services/leads.convert_lead` (which today copies client, brand/model, IMEI/serial and complaint) with a check-in–aware path:

| Source | Destination |
|---|---|
| Phone (+ name, email) | The existing client found by phone, otherwise a new client |
| Identifier (by `identifier_kind`) | The device this shop already has with that VIN / HIN / IMEI / serial (or plate) if it exists, re-linked to the client if needed. Otherwise a new `Device` with `vin` / `hin` / `imei` / `serial`, plus `brand`/`model` and `custom_data.year` from the decode, and `asset_group` and `template_key` from the template |
| Job type | `Order.type_id` (so its statuses, checklist and required signature apply) |
| Complaint answer | `Order.complaint` |
| Reading | `Order.custom_data[mileage\|hours]`, and the latest value on `Device.custom_data` |
| Custom-field answers | `Order.custom_data` / `Device.custom_data` by the field's entity |
| Signature | `DocumentSignature(doc_type="check_in", signer_name, signature_png, signer_ip, doc_hash=terms_hash)` |
| Photos (key drop) | `Order.intake_photos` |
| Key tag | `Order.custom_data.key_tag` (printable as a variable on key-tag templates) |
| Location | `Order.location_id` = the point's location |
| Lead | marked won, `converted_order_id` set (as today), check-in state → `converted` |

The conversion is idempotent: converting a converted check-in again returns the same order.

### 7.4 Counter scan
The customer's status page shows a QR code of the `status_token`. When staff scan it with a USB or Bluetooth scanner in the Inquiries search field, or with **Tekly Go**, the matching check-in drawer opens straight away. A code from another shop resolves to nothing (404).

## 8. Settings

**Settings → Appointments → Self check-in**:
- An on/off switch for the shop (on by default once released; existing shops get it off until they open the page, so nothing surprises them).
- A list of **points per location**: add, rename, mode, kiosk, job types offered, overrides, **Print poster** (A4 / Letter PDF with the QR, the shop name and the point name, rendered through the document pipeline), **Download QR** (PNG/SVG), **New code** (rotate, with a confirmation), scans and submits.
- **Preview** opens the live form for that point in a side panel, with test mode on: the check-in is created flagged as a test and deleted after 1 hour.

**Settings → Forms → (job type) → field**: new **Ask at check-in** switch, with help text saying the field will be shown to customers.

## 9. Notifications

These go through the existing rules engine (`services/notification_rules.py`) as new events, so shops can edit the wording and channels:

| Event | Default | To |
|---|---|---|
| `checkin.created` | SMS: "You're checked in at {Location}. You're #{Queue}. Track: {Status URL}" | Customer |
| `checkin.created` | In-app alert: "{Name} checked in, #{Queue}, {Asset}" | Staff at that location |
| `checkin.called` | SMS: "You're up at {Location}, please come to the counter." | Customer |
| `checkin.key_drop` | In-app alert: "Overnight drop-off: {Name}, key tag {Tag}" | Staff at that location (sent when the shop opens) |

SMS goes out with the shop's own SMS credentials; without them it falls back to the simulated adapter, as the other messages do today. The webhook `inquiry.created` fires as today (source `walk_in`).

## 10. Security and multi-tenancy

- **Tokens, not ids.** Point tokens and status tokens are random (`secrets.token_urlsafe(24)`), unique, and never derived from org, location or name. The server works out org and location from the token alone. The public API never accepts an `org_id`, `location_id` or `point_id` from the client.
- **No leaking across tenants or customers.**
  - `identify` on the public side returns decode data only, never matches, owners or history.
  - The status page shows only first name, queue number and the customer's own summary.
  - A status token only reads its own check-in.
- **Rotation.** **New code** replaces the token straight away. The old one returns `410` with a friendly message. Existing status links keep working.
- **Abuse limits** (existing `services/ratelimit.aenforce`):
  - per point and IP: 6 submits per 5 min, 30 identifies per 5 min;
  - per point and phone: 3 submits per 30 min;
  - a hidden honeypot field.
  - **Duplicates**: the same phone at the same point while a check-in is still `waiting` updates that check-in instead of creating a new one.
- **Validation on the server** against the resolved form: required fields, types, options, length caps (as the booking request already does), and size caps on the signature and photos (same media path as intake photos).
- **PII**: the sensitive-field encryption is reused, and nothing sensitive appears in SMS text, logs or the feed.

## 11. Edge cases

| Case | Behaviour |
|---|---|
| Shop closed, point `walk_in` only | Page says the shop is closed, gives its hours and offers key drop only if the location has a key-drop point; otherwise it offers the existing online booking link |
| Queue numbering across midnight | `queue_day` is shop-local (location timezone); numbers restart each day |
| Customer leaves | Cancel on the status page, or staff mark no-show; either way they leave the queue and the positions of the people behind update |
| Unknown or invalid identifier | Accepted as typed, with a "check this" hint shown to staff in the drawer; never blocks check-in |
| Device already belongs to another client | Linked on convert with the existing "seen before / different owner" warning shown to staff (internal only) |
| Job type deleted after check-in | The form snapshot still shows the answers; convert falls back to the default job type |
| Point deactivated mid-queue | Existing check-ins carry on; new scans get `410` |
| Location with many points | All points feed one queue per location; the drawer shows which point each check-in came from |

## 12. Metrics

- Check-ins per day per location, and the share of walk-ins that convert to a job
- Median time from check-in to being called (also feeds the wait estimate)
- Abandon rate (cancelled + no-show ÷ check-ins)
- Scan → submit conversion per point (find posters nobody uses)
- Key-drop jobs started the next morning

## 13. Build plan (one PR)

1. **Backend**
   - Migration (two tables + the `ask_at_checkin` column).
   - Models.
   - `services/checkin.py`: form resolution, queue numbering, wait estimate, convert mapping.
   - Public and staff routers.
   - Rules-engine events.
   - Backfill of a `Front door` point per location, plus creation on new locations.
2. **Frontend**
   - Public check-in and status pages.
   - Walk-ins view and sidebar badge.
   - Check-in drawer with Call next / No-show / Convert.
   - Settings section and point editor.
   - **Ask at check-in** switch in Forms.
   - Counter scan hook in Inquiries search.
3. **Tests**
   - **Backend**:
     - form resolution per template (auto / marine / phone) and per point;
     - the public form never exposes internal fields;
     - token isolation (another org's token or status token → 404 / 410);
     - rotation;
     - rate limits and duplicate phone;
     - queue numbering under concurrency and day rollover;
     - after-hours mode switch;
     - convert mapping, field by field;
     - convert is idempotent;
     - the signature becomes a `DocumentSignature`;
     - auto-print runs on convert.
   - **Frontend**:
     - the form renders from a resolved definition;
     - required and show-when rules;
     - kiosk reset;
     - Walk-ins actions;
     - the badge;
     - Settings points CRUD.
   - **E2E** (Playwright): scan QR → check in → staff Call next → work order prefilled.
4. **Checks**: ruff, alembic single head, full pytest (SQLite and Postgres shards), tsc, UI-text lint, vitest, and the CI E2E job.

## 14. Open questions

1. Should **Call next convert immediately**, or only mark the customer as called so staff can convert after talking to them? *Proposed:* only mark as called by default, with a per-location setting to convert straight away.
2. Should a **counter tablet in kiosk mode** need a staff PIN to leave kiosk mode? *Proposed:* yes, reusing the Tekly Go PIN.
3. Should the **check-in SMS** be sent when the shop has no SMS provider connected? *Proposed:* no; the status page link is shown on screen instead.
4. Memberships: once that spec lands, check-in recognises members by phone and shows included benefits. It's kept out of this PR.
