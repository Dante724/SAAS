# Tekly Go (browser shortcut app): spec

Status: **draft for owner approval**. Date: 2026-10-23. This replaces the native plan for now.

- The owner decided that shops install Tekly Go **from the browser**: no App Store and no developer account.
- A native store app comes **later**. Its blueprint is kept in [`tekly-go-native-spec.md`](tekly-go-native-spec.md).
- Plan: [`tekly-go-pwa-plan.md`](tekly-go-pwa-plan.md).
- Building waits for the one-shop QR pilot. The pilot picks the M1 order.

Facts from the web carry a source number, listed in §16.

---

## 1. What it is

Tekly Go is a **shortcut app**: a handful of one-tap shop-floor actions, installed from the browser to the phone's home screen. It runs at `/go` and is the same React code as today's Go PWA, rebuilt to feel premium. Everything else stays in the web app.

The six shortcuts:
1. **Scan a label** → the job.
2. **Change status** and tell the customer.
3. **Photo** onto the job.
4. **Find** a job by name, phone (last 4 digits) or WO number.
5. **Check in** a walk-in and print its label.
6. **Owner alerts** + a one-screen glance.

**Install, from the shop's side:**
1. The owner opens **Settings → Tekly Go** in the web app and sees a QR code.
2. Staff scan it with their phone camera.
3. A friendly page shows the 2 steps for that phone:
   - **iPhone:** Share → Add to Home Screen. Since iOS 26, every home-screen site opens as a web app by default [S1].
   - **Android:** one "Install" button.
4. The Tekly Go icon is on the home screen, signed in.

## 2. Goals and non-goals

**Goals for v1 (M1)**: the same bar as the native plan.
- Scan → job in **under 2 s** (warm).
- Status + text in **at most 3 taps**, and it works offline.
- **No photo is ever silently lost**, as long as the app is reopened.
- Find a job in **under 3 s**.
- Check in a walk-in with a label in **under 60 s**.
- Owner pushes only for things that need them.
- **Install in under 1 minute** from the QR code.
- Light by default and premium (§5).

**Non-goals for v1** (from the council):
- payments / PIN on staff phones (the payment-link QR stays);
- the Tasks tab;
- a Clock tab (the timer goes on Job; shift clock-in moves to More);
- the role picker (role derived from permissions);
- the Estimates toggle;
- inventory, invoices, reports, settings, a full chat inbox.

**Not possible from a browser** (needs the later native app): home-screen widgets, Siri / App Shortcuts, Live Activities, lock-screen widgets, and Tap to Pay [S10][S11]. On iPhone, also: long-press icon shortcuts, the share-sheet target, background sync and NFC [S4][S5][S6][S7].

## 3. Users and jobs to be done

The role is derived from `GET /api/auth/me`. More offers a "Show me…" override, saved per device.

| Role | How it's derived | Their jobs, most frequent first |
|---|---|---|
| **Bench tech** | No `can_create_orders` and no reports permissions | 1. Scan a label and see the device, fault, passcode, watch-outs and promised time. 2. One tap for Ready / Waiting for part / Needs approval, then text the customer. 3. Before and after photos. 4. Start or stop the job timer. |
| **Counter / desk** | `can_create_orders`, without owner or report permissions | 1. Find a job by name or last 4 digits; see status and balance. 2. Check in a walk-in with a label. 3. Template text when a job is Ready or a part arrives. |
| **Owner** | `is_owner` or reports permissions | 1. A push for big approvals, refunds, and jobs overdue against their promise. 2. The glance: promised-not-ready, overdue, ready for pickup, revenue today. 3. Read a job's history and photos. |

## 4. Screens and flows

The screens and flows are the same as the native spec (§4 there), adapted for the browser. In short:

- **Tab bar:** our own flat white bar with a hairline top, 4 tabs: Today · Find · Scan · More. **"+" (Check in)** sits in the Today and Find headers, not a floating button. The tab bar is hidden inside Check-in.
- **Today:**
  - one grouped list in **shop timezone**, with role-ordered sections (tech: Late · Due today · In progress · Waiting; desk: Ready · Late · Due today · Waiting for part; owner: Glance · Late · Needs approval · Ready);
  - **row:** avatar | **customer** + due ("2 days late" in red) | "iPhone 14 Pro · Cracked screen" + small mono WO number;
  - empty sections are hidden; no zero tiles;
  - **pull to refresh**;
  - a cached query layer, so switching tabs never flashes a skeleton.
- **Scan:**
  - the camera opens at once and decodes with the native `BarcodeDetector` on Android, or the `barcode-detector` ponyfill (zxing-wasm, **self-hosted .wasm** so it works offline) on iPhone, where Safari still has no BarcodeDetector [S3];
  - one long-lived camera stream on one route, because iOS can re-prompt for the camera on route changes in home-screen apps [S12];
  - then `routeScan()` → `/api/scan/resolve` (direct `/go/job/…` links skip the round trip);
  - "Type a number" and a torch toggle are always there.
- **Job:**
  - caption `WO-00412 · Booked Tue 14:05`; **large title = customer**; subhead = device · problem; status pill + due;
  - 5 actions: Status · Message · Photo · Call · Timer;
  - watch-outs strip (passcode set → "Reveal", audited);
  - groups: Details · Photos · Charges (**balance due in ink**) · Signature · History;
  - a **docked action bar** that never covers content.
- **Status + notify:**
  - a sheet of allowed statuses, the suggested next one first;
  - optimistic update; a 409 restores the old status and explains why in plain words;
  - a "Tell {name}?" chip opens the Message sheet, which opens the phone's SMS or WhatsApp app with the text filled in (the existing `/api/mobile/…/compose` handoff) and asks "Sent?" on return.
- **Photo:**
  - the camera via `<input type="file" accept="image/*" capture>` (multi-shot);
  - resized on the phone to 1600 px, JPEG q≈0.72;
  - **stored in IndexedDB first**, then uploaded (§7);
  - label chip: Before / After / Damage / Intake.
- **Find:** one field, focused on open, matching WO number, any 4+ phone digits, name and IMEI. Results show the balance. Recent searches are kept. A Bluetooth keyboard-wedge scanner works in the field.
- **Check in** (4 steps, tab bar hidden):
  1. customer (phone-first typeahead);
  2. device (kind, brand/model, **Scan IMEI**, colour, passcode);
  3. problem chips + promised-time presets + optional quote + photos;
  4. terms + signature + consent.

  **Create** calls one idempotent intake call, then **Print label** opens the label PDF. On iPhone this goes through the share sheet (`navigator.share({files})`), because `window.print()` in a home-screen app is unreliable [S13]. **Text tracking link** follows. Drafts survive closing the app.
- **More:** account, notifications per kind, signed-in devices ("sign out other phones"), appearance (Light / Dark / System, **Light by default**), Face ID lock (passkey, if the device test passes, §8), uploads waiting, "How to install on another phone", sign out, version.
- **Owner glance:** 4 tappable figures (promised-not-ready, overdue, ready for pickup, revenue today), top of Today for owners.

## 5. Design language

This is the council's language, the same as the native spec §6, built with web primitives:

- **Light by default** whatever the phone setting; dark only by choice in More.
- **Type:**
  - Geist, **self-hosted** (no render-blocking Google Fonts);
  - 5 steps: 28/34, 20/25, 16/22, 14/19, 12/16;
  - tabular figures; Geist Mono only for WO numbers and IMEI;
  - sentence case; nothing below 12 px for readable text.
- **Surfaces:** canvas `#f7f8fd`, white inset grouped lists (radius 12, 0.5 px hairlines), no shadows except sheets, no cards in cards.
- **Colour:**
  - sapphire `#0059ba` on interactive things only;
  - status = dot + name in lists, with a tinted pill only in the Job header;
  - red only for late; balance in ink.
- **Motion:**
  - native-feeling page transitions with the **View Transitions API** (same-document in Safari 18+ / Chrome 111+ [S9]);
  - no-overshoot easing (~280 ms); press = highlight, never scale;
  - rows move between sections on a status change;
  - honours reduced motion.
  - **No glow, glass, clay, pulsing dots or bouncy springs.**
- **Haptics:** `navigator.vibrate` on Android only. iPhone browsers have no haptics API, so iPhone users get the visual feedback only.
- **Tokens:** already shared, since the Go app is inside `frontend-react` and uses `theme.tokens.js` through Tailwind. There is no generator step. Go adds a small scale file (type steps, spacing 4/8/12/16/20/24/32, radii 8/12/16) and a **primitives kit** (`Text`, `GroupedList`, `Row`, `Button`, `StatusDot`, `StatusPill`, `Sheet`, `Avatar`, `Field`, `Banner`, `TabBar`, `NavBar`, `ActionBar`, `PullToRefresh`). Screens use only the kit, which ends the "12 font sizes, 133 arbitrary sizes" problem.

## 6. Phone features: what works where (verified 2026-10-23)

| Feature | iPhone (home-screen app) | Android (Chrome, installed) | Milestone |
|---|---|---|---|
| Home-screen icon, full screen | Yes. Since iOS 26, sites added to the home screen open as web apps by default [S1] | Yes, with an install prompt | **M0** (install page) |
| Push notifications | Yes, **only after Add to Home Screen** (iOS 16.4+), and the permission prompt must follow a tap [S1] | Yes | **M1** |
| Icon badge (count) | Yes, `setAppBadge` (16.4+), tied to notification permission [S2] | No badge API; Android shows a dot for unread notifications [S2] | **M1** (owner: things needing them) |
| Barcode scanning | Ponyfill (zxing-wasm); `BarcodeDetector` is off by default in Safari [S3] | Native `BarcodeDetector` | **M1** |
| Long-press icon shortcuts (Scan / Today / Check in) | **No** [S4] | Yes (manifest `shortcuts`; already in the manifest) | **M2** (update the list) |
| "Share to Tekly Go" from Photos | **No** [S5] | Yes (Web Share Target) | **M2** |
| Background upload retry | **No** Background Sync [S6]. Retries happen when the app opens or comes online | Yes (Background Sync) | **M1** |
| NFC tag → job | **No** [S7] | Yes (Web NFC, Chrome only) | **M2** |
| Keep screen awake on Scan / Job | Yes on iOS 18.4+ (needs a device test) [S8] | Yes | **M1** |
| Page transitions | View Transitions, Safari 18+ [S9] | Chrome 111+ | **M0** |
| Siri / Shortcuts app | Only a user-made Shortcut with "Open URL". It may open in Safari rather than the app [S11, unverified] | Same idea via Google Assistant routines | **M2** (a how-to card, after a device test) |
| Widgets, Live Activities, lock screen | **No** [S11] | **No** | Later native app |
| Tap to Pay | **No**: needs the native Stripe SDK [S10] | **No** | Later native app (or a Stripe smart reader via the JS SDK) |
| Offline storage | Home-screen apps are **exempt from Safari's 7-day storage wipe**; `storage.persist()` is supported (17+), and eviction is least-recently-used when the device is full [S14] | Persistent once installed | **M1** |

## 7. Offline and photo-upload reliability

- **Reads:**
  - TanStack Query, with its cache persisted to IndexedDB;
  - the service worker (`public/go/sw.js`, existing) keeps the shell and a short list of API reads (Today, Job, statuses, me) network-first;
  - "Offline · updated 14:02" shows when stale.
- **Writes:**
  - the existing tested **encrypted offline queue** (`lib/offlineQueue.ts`: AES-GCM with a non-extractable key, IndexedDB), with its replay rules kept;
  - each item gains an **`Idempotency-Key`**, so a replay never duplicates a job, photo or message.
  - Replay runs when the app opens, on `online`, on `visibilitychange` → visible and, on Android, on Background Sync [S6].
- **Photos:**
  - The resized JPEG blob is written to IndexedDB with a row `{id, order_id, label, state: pending|uploading|uploaded|attached|failed, attempts}` **before** any network call.
  - Upload uses multipart with `Idempotency-Key = photo id`, then `intake-photos/add`. The blob is deleted only when `attached`.
  - After 5 failures the row shows red on the job and in More → Uploads with **Retry**.
  - On iPhone, uploads only run while the app is open, so after a check-in the app shows "3 photos uploading, keep Tekly Go open" until done.
  - We call `navigator.storage.persist()` after install [S14].
- **Conflicts:** a refused change shows on the job ("Couldn't change to Ready: signature needed") with *Fix* or *Discard*.

## 8. Sign-in and security

- **Sign-in:** the same as the web, with email + password and the 2-step code (`X-MFA-Required`), using the existing sliding session (24 h access, up to 30 days).
- **Per-device sessions** (prerequisite B0b): each phone is a session; More → Devices and the web Team page can sign a phone out.
- **Install keeps you signed in:** the install page carries a **one-time install link** (short-lived, single-use code) so staff don't type a password on the phone. They scan the QR in the web app, already signed in on the computer, and the phone arrives signed in as them.
- **App lock:**
  - an optional **passkey with Face ID / fingerprint** (the existing `lib/passkey.ts`), after 10 min idle (existing `lib/idle.ts`);
  - passkeys inside an iPhone home-screen app are **not confirmed by a source** [S15], so M0 includes a device test, with sign-in again as the fallback;
  - the passcode reveal asks for the lock too.
- **Shop-only access:** when it's on, 403 `network_not_allowed` shows "Connect to the shop Wi-Fi" and queued writes are held, not dropped (existing).
- **Data on shared phones:** sign-out wipes the caches, the queue key and photo blobs (the SW's existing `CLEAR_DATA`).

## 9. Updates and versioning

- There are no store reviews: a deploy is live at once.
- **A stale app can't break things:**
  - every request sends `X-Tekly-Client: go-web/<build>` (the pilot kit added the header);
  - the service worker checks for a new version on launch and when the app returns to the foreground, then shows "New version ready · Reload" and never reloads mid-task;
  - the server can answer **426 `client_too_old`** for a build below a configured minimum, and the app then reloads itself.
- **API:**
  - Go keeps calling the shop's normal `/api/*` plus a few new Go aggregates under **`/api/go/*`** (§11).
  - These ship in the same deploy as the app, so there is no separate versioned contract.
  - When the native app comes later, these become `/api/mobile/v1`, as the native spec describes.

## 10. Push notifications

- **Channel:** Web Push. The backend already has VAPID keys, `PushSubscription`, `/api/push/subscribe` and `services/push.notify`.
- **iPhone:** push works only after Add to Home Screen [S1]. The install page and Today show "Turn on alerts" **after** install, and the prompt follows a tap.
- **New kinds:** `job.overdue` (promised time passed, not ready; once per job), `approval.needed` (over an owner-set amount), `refund.requested`, `part.arrived`, `daily.summary` (opt-in, at closing time). Existing kinds stay.
- **Role defaults:** owner = approvals, refunds, overdue. Tech = assigned, customer replied, approval received. Desk = part arrived, customer replied, estimate approved.
- **Badge** (iPhone): the count of things needing this person, set from the push handler and on app open [S2].
- **Payload:** customer + device as the title, what changed as the body, the deep link as `data.url`. **No phone numbers or message text.**

## 11. Backend gap list

Checked against `launch/audit-fixes` and the Go branches (`450a9d2`, PR #10/#11).

**Prerequisites (owner-approved, in progress in their own sessions):**
- **B0a:** Go backend from PR #10/#11 (idempotency, `intake-photos/add`, `/api/mobile/*` messaging, `due_at`, shop-tz dashboard, signature, drop-off terms).
- **B0b:** per-device sessions (`auth_sessions`, sign out a device).

| # | Endpoint | Today | Needed |
|---|---|---|---|
| B1 | `GET /api/go/board?scope=mine\|shop` | PARTIAL: `/api/tech/dashboard` lacks age/balance/status colour; `/api/orders/board` lacks complaint/due/age and has a **UTC-vs-shop-time overdue bug** | Rows with `status{key,name,color,group}`, `client{name,phone_last4}`, `device.label`, `complaint`, `due_at`, `is_late`, `age_days`, `assignee`, `balance_due`; sections in shop tz; ETag |
| B2 | `GET /api/go/jobs/{id}` | PARTIAL: `/api/statuses/allowed/{id}` returns keys only and mixes estimate keys | One aggregate: header, `allowed_statuses` with names/colours/`needs`, `suggested_next`, watch-outs, photos, charges + `balance_due`, last 5 history items |
| B3 | `PUT /api/go/jobs/{id}/device/passcode`, `POST …/passcode/reveal` | MISSING: the `Device.passcode_enc` column exists but nothing writes it | Encrypted write; audited reveal |
| B4 | `GET /api/go/search?q=` | PARTIAL: phone search is a raw substring, so last-4 fails on formatted numbers | `phone_digits` column + index; WO / name / IMEI; board-row shape + balance |
| B5 | `POST /api/go/intake` | MISSING: needs two calls; passcode not writable | One idempotent call: client (find or create by E.164) + device + complaint + passcode + due + photos + signature + consent → job + `label_pdf_url` + `tracking_url` |
| B6 | `GET /api/go/jobs/{id}/label.pdf` | PARTIAL: the pilot kit's `/api/pilot/job-labels` makes label PDFs in bulk | A single-job label PDF at template size, QR = `/go/job/{id}` link |
| B7 | `POST /api/go/uploads` | PARTIAL: `/api/media` is base64 JSON only, no idempotency | Multipart with `Idempotency-Key` |
| B8 | New push kinds + badge count | MISSING | `job.overdue` sweep (shop tz), `approval.needed` (with an `Organisation.approval_push_threshold`), `refund.requested`, `part.arrived`, `daily.summary`; `GET /api/go/badge` |
| B9 | `GET /api/go/glance` | PARTIAL: the company dashboard uses the **UTC** day and has no promised-not-ready count | `{promised_not_ready, overdue, ready_for_pickup, revenue_today}` in shop tz |
| B10 | `POST /api/go/install-links` + `POST /api/go/install-links/{code}/redeem` | MISSING | A one-time, 10-minute, single-use code tied to the staff member who is signed in on the web, creating a device session on redeem |
| B11 | Minimum-build check | MISSING | `GO_MIN_BUILD` setting; 426 `client_too_old` on `/api/go/*` when `X-Tekly-Client` is older |

Not needed in the browser version (unlike the native plan): store config, refresh-token pairs, app-link files, App Store account deletion, and native push devices.

## 12. Where the code lives

It stays at **`frontend-react/src/go/`**, building on the Go PR stack: #10 → #11 → the quick-fixes branch (`claude/go-quick-fixes-2998cc`, light theme, grouped rows, docked actions). New folders:
- `src/go/ui/`: the kit;
- `src/go/data/`: query client, persisters, hooks;
- `src/go/features/`: today, job, scan, find, checkin, push, glance;
- `src/go/install/`: the install page.

Backend aggregates go in `backend/routers/go.py`. The `lib/` modules stay as they are, already tested.

## 13. Builds, tests and release

- **CI:**
  - the existing `frontend` job (typecheck, Vitest with coverage floor, build) covers Go;
  - add **Playwright mobile projects** (WebKit iPhone and Chromium Pixel viewports) for the Go flows to the existing `e2e` workflow;
  - backend tests in the `test` job.
- **Real-device checks** (they can't be automated) before each milestone:
  - an iPhone on the current iOS: install, camera scan, push, badge, passkey, label print via the share sheet, photo queue after closing the app;
  - one mid-range Android: install prompt, shortcuts, share target, NFC.
- **Release** is the normal web deploy. The SW update banner handles phones running an old version.

## 14. Metrics per phase

| Phase | Metric | Target |
|---|---|---|
| Pilot (now) | Share of status changes and photos done on the phone; how the counter looks jobs up | Picks the M1 order |
| M0 | Install completion: install page opened → app opened from the home screen | ≥ 70% |
| M1 | Scan → job (p50, warm) | < 2 s |
| M1 | Status changes from the phone / all, per active shop | ≥ 40% by week 4 |
| M1 | Jobs with ≥ 1 phone photo | ≥ 50% |
| M1 | Check-in → label (p50) | < 60 s |
| M1 | Find → job (p50) | < 3 s |
| M1 | Queued writes replayed within 10 min of reconnecting | ≥ 99% |
| M1 | Photos reaching `attached` within 24 h | ≥ 99.9%, 0 silent losses |
| M1 | Staff who allowed push (iPhone: after install) | ≥ 60% |
| M2 | Android shortcut and share-target uses / week; owner weekly opens | Baseline, then ≥ 3 owner opens / week |

Events go to our own backend (`POST /api/go/events`, batched), extending the pilot kit's tracking.

## 15. Open decisions (one at a time)

1. ~~**One-time install link (B10).**~~ **Decided 2026-10-23:** yes. The QR carries a 10-minute single-use code, so staff arrive signed in.
2. ~~**Face ID lock with passkeys.**~~ **Decided 2026-10-23:** yes, after 10 min idle, only if the iPhone device test (plan M0.4.7) passes. Fallback: sign in again.
3. **Where to build:** on top of the quick-fixes branch (which stacks on PR #10/#11). *Recommend:* yes, merging the stack into `launch/audit-fixes` first (the B0a session is doing the backend half).
4. **iPhone Shortcuts how-to card** ("Hey Siri, scan in Tekly Go" via an Open URL shortcut). *Recommend:* only if a device test shows it opens the installed app rather than Safari.

## 16. Sources (checked 2026-10-23)

- [S1] Web Push for home-screen web apps (iOS 16.4); Declarative Web Push (Safari 18.4); iOS 26 home-screen sites open as web apps: https://webkit.org/blog/13878/web-push-for-web-apps-on-ios-and-ipados/ , https://webkit.org/?p=16535 , https://webkit.org/?p=16993 , https://caniuse.com/push-api
- [S2] Badging API: https://caniuse.com/mdn-api_navigator_setappbadge , https://developer.chrome.com/docs/capabilities/web-apis/badging-api
- [S3] BarcodeDetector (disabled by default in Safari through 27.2) and the `barcode-detector` zxing-wasm ponyfill: https://caniuse.com/mdn-api_barcodedetector , https://www.npmjs.com/package/barcode-detector
- [S4] Manifest shortcuts (not on iOS): https://caniuse.com/mdn-manifests_webapp_shortcuts
- [S5] Web Share Target (not on iOS): https://caniuse.com/mdn-manifests_webapp_share_target
- [S6] Background Sync (not on iOS): https://caniuse.com/background-sync
- [S7] Web NFC (Chrome Android only): https://caniuse.com/webnfc
- [S8] Screen Wake Lock in home-screen apps (fixed, reportedly iOS 18.4): https://bugs.webkit.org/show_bug.cgi?id=254545 , https://caniuse.com/wake-lock
- [S9] View Transitions: https://caniuse.com/view-transitions , https://caniuse.com/cross-document-view-transitions
- [S10] Stripe Terminal JS works only with internet readers; Tap to Pay needs the native SDKs: https://docs.stripe.com/terminal/overview , https://docs.stripe.com/terminal/references/api/js-sdk
- [S11] Apple Shortcuts "Open URLs" action: https://support.apple.com/guide/shortcuts/apd621a1ad7a/ios. Widgets and Live Activities need native WidgetKit/ActivityKit.
- [S12] Camera re-prompts in standalone mode: https://bugs.webkit.org/show_bug.cgi?id=215884 , https://developer.apple.com/forums/thread/788518
- [S13] `window.print()` in standalone mode (old reports of failure; to test): https://developer.apple.com/forums/thread/22911
- [S14] Storage policy (home-screen apps exempt from the 7-day cap; persist; quotas): https://webkit.org/tracking-prevention/ , https://webkit.org/blog/14403/updates-to-storage-policy/
- [S15] Passkeys on iOS (standalone behaviour unconfirmed): https://passkeys.dev/docs/reference/ios
- EU note: home-screen web apps work in the EU on WebKit; no 2026 change found: https://open-web-advocacy.org/apple-dma-review/
