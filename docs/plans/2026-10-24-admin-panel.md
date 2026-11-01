# Tekly admin panel: plan (24 Oct 2026)

The operator's back office at `/admin`, where Tekly runs its shops. A shop's own settings are not part of this plan.

The plan was reviewed by a council: PM, founder, future support hire, engineer, tester and security.

**Assumptions:**
- 50–100 paying shops in the first 6 months, in India, the US and the UAE.
- Paddle is the chosen merchant of record but is **not connected yet**.

## Where we start
The backend already has these at `/api/admin`:
- separate login (rate-limited);
- shop counts by stage;
- shop list and search;
- shop detail;
- edits to a shop's plan, status, discount, dates and staff access;
- platform coupons.

The page itself is `frontend/admin.html`, about 300 lines of vanilla JS.

What the review found (each item checked in the code):

| Severity | Finding | Where |
|---|---|---|
| Critical | Admin sign-in is a password only, with no 2-step check, and gives access to every shop | `admin.py:64`, `platform_auth.py` |
| Critical | No admin action is logged: no who, no before and after values | `admin.py:148–261` |
| High | The token lasts 12 hours and is kept in `localStorage`. Logout only clears it in the browser, and a token can't be revoked | `platform_auth.py:28`, `admin.html:131,166,171` |
| High | The admin page allows inline scripts (`unsafe-inline`), so any XSS takes over a god-mode token | `observability.py:84` |
| High | Admin and shop tokens share one signing key and are told apart only by a `scope` claim | `platform_auth.py:29,41` |
| High | No password rules for admins. A dev default exists outside production | `platform_auth.py:54–79` |
| Medium | Login is faster for unknown emails (timing leak) | `admin.py:70` |
| Medium | Plan is free text, money is a float, coupon codes aren't checked, and a duplicate coupon gives a 500 | `admin.py:161,167,233–239` |
| Medium | Every list loads every shop, with no paging | `admin.py:96,107` |
| Low | Each route adds the auth check itself; nothing structural stops a new route forgetting it | `admin.py` |

## Account ID (decided)
Every shop gets a short ID, for example `TK-7K4M-92Q`:
- **Characters:** 31 symbols with no 0/O/1/I/L, drawn at random rather than counting up.
- **Check character:** the last character catches every single-character typo and every swap of neighbouring characters. This was prototyped: it caught 630,000 of 630,000 substitutions and 17,413 of 17,413 swaps.
- **Uniqueness:** a UNIQUE column in the database plus a retry on clash. A forced clash retried correctly.
- **Lifetime:** never reused or changed, and never used to sign in.
- **Lookup:** accepts lowercase, spaces and missing dashes, and rejects a bad check character before touching the database.
- **Backfill:**
  1. Add a nullable column with a unique index.
  2. Backfill in Python, in batches, retrying on clash.
  3. A separate migration sets it NOT NULL.

  This works on both SQLite and Postgres.
- **Where it shows:**
  - the shop's Settings → General, with a copy button;
  - admin search and every admin row;
  - Paddle `custom_data` once Paddle is connected.

## Admin roles and permissions
The owner limits what each admin can see and do. For example, a sales person can see trials and sign-ups in their countries and give a small discount, but can't see revenue, read a shop's data or add admins.

### Every request passes six gates (server side; the screen only hides what the server already refuses)
1. **Signed in:** a live session with 2-step done.
2. **Permission:** does this admin's role allow the action? Unticked means no, so new permissions start off.
3. **Scope:** which shops they can reach. For example, only India and the UAE, only shops assigned to them, or only trials. Lists, search, counts and exports are filtered in the database query, and opening a shop outside their scope by its ID returns "not found".
4. **Field mask:** which fields they see on a shop they can reach. Hidden fields are removed by the server, never just greyed out.
5. **Limits:** caps on how far they can go, for example a discount up to 10% for up to 3 months, or a trial extension of up to 7 days. Over the cap becomes an approval request to the owner.
6. **Action log:** every allowed and refused action is written, with who, what, which shop and why.

### Permission list (same pattern as the shops' own roles in `backend/services/permissions.py`)
| Group | Permissions |
|---|---|
| Shops | View shop list · View shop profile · Add notes · Assign shops to people |
| Money | See revenue and MRR · See what a shop pays · Give discounts · Extend trials · Change plan |
| Codes | Create coupon codes · Create partner codes |
| Customer data | See owner contact details · See staff list · Export a shop's data |
| Account actions | Suspend or unsuspend · Change owner or email · Run imports |
| Support | Request support access (still needs the owner's grant) |
| Admin | Add or remove admins · Edit roles · Read the full action log · Approve requests |

### Fields that can be hidden per role
Revenue and MRR, what the shop pays, owner email and phone, staff list, notes, the shop's own numbers (jobs, sales), and payment history.

Each field is set to one of three levels per role:
- **Hidden:** removed by the server.
- **Own book only:** shown only for shops assigned to this person.
- **Shown:** visible for every shop in scope.

"Own book only" is what lets an account manager see what *their* shops pay without seeing every shop's revenue or the company MRR.

### Ready-made roles (the owner can copy and adjust them)
| Role | Sees | Can do | Can't |
|---|---|---|---|
| **Owner** | Everything | Everything, and approves requests | — |
| **Sales** | Trials, sign-ups, plan, country and health for shops in their scope; owner contact only for trials | Add notes, extend a trial up to 7 days, give a discount up to 10% for 3 months, create codes, email trials | See revenue or what shops pay, see staff or shop data, export, suspend, support access, manage admins |
| **Account manager** | Their book (assigned paying shops): plan, what each pays, renewal date, health, usage, owner contact, notes, payment status. Company-wide numbers are hidden; they see only their book's total | Add notes, email their shops, give a retention discount up to 15% for 3 months, extend a grace period up to 7 days, request a plan change (upgrade or downgrade goes to the owner), hand a shop to someone else | See shops outside their book, see company revenue, export data, suspend, support access, change owner or email, manage admins |
| **Support** | Shop profile, staff list, health; no revenue | Add notes, request support access, run imports, change an owner's email after checks | Discounts, plan changes, codes, manage admins |
| **Finance** (later) | Revenue, what shops pay, payment history | Plan changes, refunds through Paddle, discounts of any size | Support access, imports |

### Sales and account management working together
- **Book of shops:** every shop has one assigned person. Sales owns trials. When a trial becomes paying, the shop moves to the account manager. The move happens automatically on the first payment, by a rule the owner sets (by country, or round-robin), or by hand. Every move is logged, and the shop's notes and email history go with it.
- **Same person doing both jobs:** give them the ready-made **"Sales and account management"** role, the two combined with the larger of each cap. Each person has one role. Mixing several roles would make "which cap applies?" unclear.
- **Their own Today list:**
  - **Sales:** trials ending, trials with little use, and new sign-ups in their countries.
  - **Account manager:** renewals in the next 30 days, failed payments, shops whose use is dropping, and discounts about to end, all for their book only.
- **"My book" numbers:**
  - **Account manager:** number of shops, their monthly total, renewals coming, and at-risk shops.
  - **Sales:** trials and how many converted this month.

  These never show company totals unless "See revenue and MRR" is on.
- **Shared history:** notes and emails sent from admin show on the shop's timeline for whoever owns it next, so nothing is lost at handover.
- **When someone leaves:** the owner deactivates them. Their sessions end at once, their open approval requests are cancelled, and the owner reassigns their whole book in one step. Nothing can be left with no owner.
- **Later:** targets and commission. Conversion and renewal numbers per person are already in the action log and the book, so a report can be added when you pay commission.

### Approvals
When a sales person or account manager asks for more than their cap (for example 30% off), or an account manager asks for a plan change, it becomes a request with a reason on the owner's Today screen. Approve or decline in one click, and both are logged. A request expires after 7 days.

### Data model
- **`admin_roles`:** id, name, permissions as an allow-list, field levels (hidden, own book, shown), scope rules (countries, own book only, stages such as trial or paying), and limits (max discount %, max discount months, max trial or grace days).
- **`platform_admins`:** gains `role_id`, `token_version`, the 2-step secret and recovery codes, and last login.
- **`organisations.assigned_admin_id`:** one owner per shop, plus **`admin_assignment_log`** (shop, from, to, why, when) for the handover history.
- **`assignment_rules`:** how converted trials are handed over (by country or round-robin).
- **`admin_approval_requests`:** requester, shop, action, amount, reason, status, decided by, and when.

### How it is enforced in code
- **Permission check:** a `require_admin_perm("give_discount")` dependency on every route. A route test fails if a mutating route has none.
- **Scope check:**
  - one `scope_shops(query, admin)` used by every list, search, count and export;
  - `get_shop_or_404(admin, shop_id)` used by every detail route;
  - an "out of scope" shop looks the same as a missing one.
- **Field mask:** one serializer per shop view that drops hidden fields based on the role. A test checks that every hidden field is absent from every response.
- **Limits:** checked in the service function, not only the route. Over the cap returns "approval needed" and creates the request.
- **Changes take effect at once:** editing a role bumps the `token_version` of everyone with it, so the next request uses the new rules. No log-out needed.
- **Protecting the top role:**
  - nobody can give themselves more than they have;
  - only the owner edits roles;
  - the last owner can't be removed;
  - role changes are logged with before and after values.

### Screens
- **Admins:** each person with their role, scope and last login, plus invite and deactivate.
- **Role editor:** a grid of permissions with on/off switches, the hidden fields, the scope and the limits, plus a "preview as this role" view of a sample shop that shows exactly what the role sees.
- **Approvals:** a list on Today.
- **Shop profile:** "Assigned to", with a hand-over button and its history.
- **My book:** for sales and account managers, their shops and numbers.
- **Deactivate person:** asks you where their book goes.

### Tests
- Each ready-made role against every admin route, as an allowed and denied grid.
- Every hidden field absent from every response for that role.
- Out-of-scope shop IDs return "not found" on detail, edit, notes and export.
- Counts and search only include in-scope shops.
- A discount 1% over the cap creates a request instead of applying.
- A role edit applies on the next request.
- Self-escalation is refused, and the last owner can't be removed.
- Refused actions are logged.
- **Account manager:**
  - sees what their own shops pay, but that field is absent for in-scope shops outside their book;
  - company MRR is absent from every response;
  - a 16% retention discount becomes a request.
- **Handover:** the first payment moves a shop from sales to the account manager, the history follows the shop, and the previous owner loses "own book" fields at once.
- **Leaving:** deactivating a person ends their sessions, cancels their requests, and blocks until their book is reassigned.

## Sprint 1: lock down the panel and give every shop an identity
Security:
- **2-step login for admins:** TOTP plus single-use recovery codes. It is mandatory, enrolled on first login, and there is a documented owner reset if you lose your phone.
- **Sessions:**
  - a 60-minute token with a session id;
  - a server-side logout that revokes it;
  - deactivating an admin or changing their password kills their sessions;
  - the admin's own signing key and an `aud` claim.
- **Login hardening:** the same response and timing for unknown emails, a minimum 12-character password with a weak-password list, and no dev-default admin unless on localhost.
- **Auth on every route:**
  - the auth check sits on the admin router itself, with login on a separate public router;
  - a test walks every `/api/admin` route and fails if one is reachable without auth.
- **Admin action log:**
  - **What a row holds:** who, what, which shop, before and after values, IP, and a reason (required for money, suspend and plan changes).
  - **Same transaction:** each row is written in the same transaction as the change.
  - **Append-only:** a database trigger blocks UPDATE and DELETE, and a hash chain makes any tampering visible.
- **Admin roles and permissions:** the owner decides what each person sees and can do (see "Admin roles and permissions" below). Sprint 1 ships the permission list, the gates, field levels, shop assignment with handover, and the Owner, Sales, Account manager, "Sales and account management" and Support roles. My book numbers and the automatic first-payment handover come in sprint 2, with Paddle.
- **A new admin app:** a separate React entry, light design, no inline scripts, and the token is not kept in `localStorage`.

Shop identity and profile:
- **Account IDs** as above.
- **Shop profile:**
  - **Account details:** plan, locations, seats, country, owner's contact details, last login, and jobs this week.
  - **Notes:** a timeline of notes and "last contacted".
  - **History:** every change, read from the action log.
- **Discount with an end date and a reason.** It reverts by itself when it ends. Money is stored as exact amounts, not floats.
- **Actions with a reason:**
  - extend a trial;
  - suspend or unsuspend, with a message the shop sees;
  - change a shop's plan from an allowed list only.
- **Housekeeping:** paging and server-side search, and coupon codes limited to `A–Z 0–9`, 3–32 characters (Paddle's rules), with a clean error on duplicates.

## Sprint 2: Paddle and the Today screen
- **Paddle connected, sandbox first.** Paddle's webhooks become the only thing that writes plan, status, renewal and discount. Local fields are marked "local" until a one-way switch hands them to Paddle.
- **Money view (read-only):** failed payments and retries, refunds, cancellations with their reasons, and renewals. All of it comes from Paddle.
- **Today screen:**
  - trials ending soon with little use;
  - failed payments;
  - discounts ending;
  - suspended shops;
  - imports waiting.
- **Email the shop:** from a few templates, with a log on the shop's timeline.
- **Duplicate signups:** a flag when the same email, phone or domain appears again (trial abuse).

## Sprint 3: helping shops safely
- **Support access, given by the owner:**
  - **Grant:** the owner turns it on in their settings for up to 24 hours and can revoke it in one click.
  - **To start:**
    - you need a fresh 2-step code and a reason;
    - the owner is emailed when it starts and ends;
    - you are alerted on any session started off-hours or unusually often.
  - **Token:** a short-lived shop token with an `act` claim (admin, grant, mode). Every request checks the grant, so a revoke works at once, and the token can't be refreshed.
  - **Read-only by default:** writes need a second, separate grant from the owner. These are always blocked:
    - password, 2-step and email;
    - owner-only settings;
    - billing and API keys;
    - bulk exports.
  - **Visible:**
    - a banner shows in the shop's app;
    - every request is logged in both the shop's log (`actor_kind = tekly_support`) and the admin log;
    - the owner can see the list.
- **Data requests:** a tracked queue for export and deletion requests with legal deadlines (GDPR, India DPDP). The owner exports their own data from their settings.
- **Change of owner or email:** the classic support ticket, with checks and a log.
- **CSV import with a dry run and undo:**
  - **Dry run:** the same code inside a rolled-back transaction shows counts and problems.
  - **Undo:** every imported row is stamped with its batch. Undo removes rows that haven't been edited or used since and reports the rest, with a 7-day window.
  - **Cleaning:** formula characters are neutralised, size and encoding are checked, and the same file can't be imported twice.

## Later (when there's a reason)
- **Full codes engine:** limits by plan and country, a generator, partner codes. Build it on Paddle discounts once Paddle is live. Until then, use a few hand-made codes.
- **Referral codes:** one per shop, free month granted by hand. Ask pilot shops first whether they would refer anyone.
- **Health score:** at 100 shops you can read the list by eye.
- **Importers from competitors:** Orderry, RepairShopr and RepairDesk, when customers ask. Until then, CSV plus manual help.
- **Other:**
  - storage and files by date;
  - map;
  - announcements;
  - per-shop feature switches;
  - Finance role;
  - IP allowlist or a separate admin hostname.
- **Tax invoices:** Paddle as merchant of record issues them. Confirm with Paddle before building anything.

## Key tests (from the tester)
- **Sprint 1:**
  - **Every route:** each `/api/admin` route needs auth and a 2-step-verified session, and shop and admin tokens are refused on each other's routes.
  - **2-step codes:** replay, clock skew and recovery codes.
  - **Roles:** each role's limits, and the last owner can't be removed.
  - **Action log:** a row on every change, none on a refused change, the database refuses edits, and a rollback removes the row too.
  - **Account IDs:** format, check character, 100k generations unique, a forced clash retries, backfill on 0, 1 and 5k shops, and lookup tolerance.
  - **Discount end date:** at the day boundary and across time zones.
- **Sprint 2:**
  - **Paddle webhooks:** signature, replay and order.
  - **The switch:** one-way, with no double-billing path.
  - **Today screen:** its counts match the shop counts.
- **Sprint 3:**
  - **Grants:** owner-only, expiry to the second, a revoke kills the token on the next request, and no reach into another shop.
  - **Act tokens:** refused on every non-GET shop route and on `/refresh`.
  - **Import:** a dry run changes nothing, undo is exact, and the bad-file cases are handled.
  - **Export:** contains no secrets.

## Open questions for the founder
1. The Paddle account: sandbox keys are needed at the start of sprint 2.
2. Who are the first hires, and is sales and account management one person or two? Which countries does each cover, and what caps do you want (the defaults are 10% for sales and 15% for account management, for 3 months)?
3. Should `/admin` live on its own hostname behind Cloudflare Access? This is cheap extra protection.
