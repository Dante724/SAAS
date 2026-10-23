# Tekly Go (native): spec

Status: **draft for owner approval**. Date: 2026-10-23. No app code exists yet. Building starts after the one-shop QR pilot reports.

Companion docs:
- Implementation plan: [`tekly-go-native-plan.md`](tekly-go-native-plan.md).
- Inputs:
  - the design council synthesis (2026-10-23);
  - the owner's design-decisions brief;
  - the competitor survey;
  - the 2026-10-18 system design for one-tap messaging and network access;
  - a backend audit of `launch/audit-fixes` and the Go PWA branch (`450a9d2`).

How to read this: each numbered section is short and can be approved on its own. Facts that came from the web carry a source number. The sources are listed in §19.

---

## 1. What we're building, in one paragraph

Tekly Go is the phone companion to the Tekly web app, built with Expo / React Native for iPhone and Android. It does a few shop-floor jobs faster than the web app can:
- scan a label and land on the job;
- change a status and tell the customer;
- photograph the device onto the job;
- find a job from a name or the last 4 digits of a phone number;
- check in a walk-in and print its label;
- tell the owner about the few things that need them.

Its edge over every repair competitor is the OS itself: widgets, Live Activities, Siri / App Shortcuts, and later Tap to Pay. The `/go` PWA stays as the no-install fallback.

## 2. Goals and non-goals

**Goals (v1 = M1)**
1. A tech gets from a scanned label to the right job in **under 2 s**, warm start.
2. A status change with an optional customer text takes **at most 3 taps**, and the app remembers it while offline.
3. Photos taken on the phone always reach the job eventually: no silent loss, retried until done.
4. The desk finds a job by name, phone (any 4+ digits) or WO number in **under 3 s**.
5. The desk checks in a walk-in, label included, in **under 60 s**.
6. The owner gets a push only for things that need them, plus one glance screen.
7. The app looks and feels native and premium. It is light by default and follows the brief (§6).

**Non-goals for v1** (decided by the council)
- **Payments and the PIN on staff phones.** The payment link QR in the web app stays. Tap to Pay comes in M3.
- **No Tasks tab.**
- **No Clock tab.** The job timer lives on the Job screen; shift clock-in moves to a widget or App Shortcut in M2.
- **No "Today shows" role picker.** The role is derived from permissions (§3).
- **No Estimates toggle on new jobs.** A check-in always creates a work order.
- Also left out: inventory, invoices, reports, settings, calendar editing, a full chat inbox, iPad-specific layouts, and staff signup in the app.

## 3. Users and jobs to be done

Role is derived from `GET /api/auth/me`. There is no picker, but More has a "Show me…" override that is saved per device.

| Role | How it's derived | Their jobs, most frequent first |
|---|---|---|
| **Bench tech** | No `can_create_orders` and no reports permissions; has assigned jobs | 1. Scan a label and see the device, fault, passcode, watch-outs and promised time. 2. One tap for Ready / Waiting for part / Needs approval, then text the customer. 3. Before and after photos. 4. Start or stop the job timer. |
| **Counter / desk** | `can_create_orders`, without owner or report permissions | 1. Find a job by name, phone or last 4 digits; see its status and balance. 2. Check in a walk-in with a label in under 60 s. 3. Send the template text when a job is Ready or a part arrives. |
| **Owner / manager** | `is_owner` or reports permissions | 1. A push when something needs them: a big approval, a job overdue against its promise, a refund. 2. A glance at promised-but-not-ready, overdue, ready for pickup and today's revenue. 3. Read a job's history and photos when a customer escalates. |

Every role sees the same app. The role only changes the order of sections on Today and which pushes are on by default.

## 4. Screens and flows

### 4.1 Navigation
- **Tabs (native tabs, 4):** Today · Find · Scan · More. Scan is a normal tab, not a floating button.
- **"+"** (Check in) sits in the Today and Find nav bars, in place of the PWA's FAB. It is hidden for roles without `can_create_orders`.
- **Pushed on the stack:** Job, Photos, History, Check-in steps. **The tab bar hides inside the Check-in flow.**
- **Sheets** (native detents) are used for Status, Message, Photo label and Passcode reveal.
- **Deep links:** `https://<app-host>/go/j/{barcode}` is a universal / app link, so the same URL opens the PWA when the app isn't installed. Labels print this as the QR.

### 4.2 Today
One grouped list in **shop timezone**, with sections in this order (role-dependent):

| Tech | Desk | Owner |
|---|---|---|
| Late · Due today · In progress · Waiting | Ready for pickup · Late · Due today · Waiting for part | Glance card (§4.8) · Late · Needs approval · Ready |

- **Row:** avatar | **customer name** + due ("2 days late" in red when late) | "iPhone 14 Pro · Cracked screen" + small mono `WO-00412`. A status dot and name sit on the right.
- **Empty sections are hidden.** There are no zero tiles. The all-empty state reads "Nothing due today. Scan a label or tap + to check one in."
- **Pull to refresh.** No refresh button. Data is cached (TanStack Query), so switching tabs never flashes a skeleton.
- When a status change moves a row between sections, it animates the move.

### 4.3 Scan → job
- The camera opens immediately (`expo-camera` `CameraView` with barcode settings [S5]). QR, Code128, EAN and DataMatrix are on.
- A **light impact** haptic fires on decode. The code goes through `routeScan()` (the ported PWA module):
  - a direct `/go/j/…` link skips the round trip;
  - otherwise the app calls `POST /api/scan/resolve`.
- **Job** → push the Job screen. **Part / asset** → a small sheet (v1 shows only the name and the jobs for that asset). **No match** → "No job for this code", with *Search instead*.
- Other inputs: a torch toggle, and manual entry ("Type a number"). A Bluetooth keyboard-wedge scanner works on the Find field.
- Phase 2 adds **NFC** tag tap → same route (§7).

### 4.4 Job
- **Header:**
  - caption `WO-00412 · Booked Tue 14:05`;
  - **large title = customer name**;
  - subhead = device · problem;
  - a tinted status pill (the only pill in the app) + "Due Thu 17:00" (red when late).
- **Five actions** (icon row): Status · Message · Photo · Call · Timer (Timer only for techs).
- **Watch-outs strip**, when present: passcode set (tap → "Reveal" sheet, audited), flagged notes, the customer's preferred channel.
- **Groups:** Details (device, IMEI mono, colour, condition, promised) · Photos (grid, labelled) · Charges (lines + **balance due in ink**) · Signature · History (latest 5, then "Show all").
- **Docked action bar** sits above the home indicator. It shows the primary next status ("Mark ready"), derived from the allowed statuses and the shop's status groups. It never covers content: the list gets bottom inset padding.

**Status + notify flow**
1. Tap Status. A sheet lists the allowed statuses (names and colours) with the most likely next one first.
2. Pick one. The UI updates at once (`changeStatus()`, ported) with a light haptic. The server confirms; a 409 restores the old status and shows the reason in plain words.
3. If the new status has a template, a "Tell {first name}?" chip appears for 6 s. Tapping it opens the Message sheet with the template filled in.
4. Message sheet: **SMS** / **WhatsApp** opens the phone's own composer with the text filled in (`/api/mobile/orders/{id}/compose` → handoff). "Send via Tekly" uses the shop's Twilio account when it's set up. Coming back to the app asks "Sent?" (Sent / Not sent).

**Photo flow**
1. Tap Photo. The native camera opens and keeps shooting until **Done**.
2. Each shot is resized on the device to a 1600 px long edge, JPEG q≈0.72, and written to the app's documents folder with a pending-upload row.
3. A label chip (Before / After / Damage / Intake) applies to the batch. The default is "Before" while status is open and "After" once the job is ready.
4. Uploads run in the background (§8). Thumbnails show on the job at once with a small progress ring and turn solid when attached.
5. The **share sheet** offers "Add to Tekly job…" (M2).

### 4.5 Find by number
- One search field, focused on open. It matches WO number, any 4+ digit run in the customer's phone, name, and IMEI or serial.
- Results use Today's row design plus a **balance due** line when there is one.
- Recent searches (5, per device) and "Recently opened" show when the field is empty.
- A keyboard-wedge scanner typing into the field + Return goes through `routeScan()`.

### 4.6 Quick check-in with label
Four steps, one per screen, with the tab bar hidden and a progress caption ("2 of 4"):
1. **Customer:** a phone number first (it finds existing customers as you type), or a name. A new customer needs only name + phone.
2. **Device:** a kind chip (Phone / Tablet / Laptop / Console / Other), brand/model with typeahead from the catalogue, then **Scan IMEI** (barcode on the box or the `*#06#` screen) or serial, colour, and an optional passcode (stored encrypted).
3. **Problem + promise:** problem chips + free text; promised time presets (Today 17:00 / Tomorrow / +3 days / pick); an optional quote amount; intake photos.
4. **Sign + label:** drop-off terms + signature, and consent toggles (SMS, WhatsApp).
   - **Create** calls one idempotent `POST /api/mobile/v1/intake`, then offers **Print label** (AirPrint / Android print of the label PDF) and **Text the tracking link**.

Drafts survive app kill. A check-in started offline is held with a clear banner and submitted when back online; its label prints after the server assigns the WO number.

### 4.7 More
Account and shop, notification settings per kind, devices signed in ("This iPhone · last used now", sign out others), Appearance (Light / Dark / System, **Light by default**), Face ID lock, help, Delete account (§11), sign out, and the version and build.

### 4.8 Owner glance + push
- **Glance card** (top of Today for owners; also the M2 widget): four figures, each tappable into a filtered list:
  - **Promised, not ready** (count, red if > 0 and past due);
  - **Overdue**;
  - **Ready for pickup**;
  - **Revenue today** (shop tz, tabular figures).
- **Owner pushes (default on):** approval over the shop's threshold, a refund requested, a job past its promised time and not ready (once per job), and the daily summary at the shop's closing time (opt-in).
- **Tech pushes (default on):** job assigned to me, customer replied on my job, approval received.
- **Desk pushes (default on):** a part arrived for a job, a customer replied, an estimate approved.

## 5. Platform and stack (verified 2026-10-23)

| Piece | Choice | Notes |
|---|---|---|
| Framework | **Expo SDK 58** (React Native 0.88, React 19.3) [S1][S2] | New Architecture is mandatory (since SDK 55) [S3]. Floors are **iOS 16.4+, Android 7+**, Xcode 26.4+, Node 22.13+ [S2]. targetSdk 36 meets Google Play's Aug-2026 rule [S14]. |
| Navigation | **Expo Router 58**, with native tabs **stable in SDK 58** (`expo-router/native-tabs`) [S4] | 5-tab cap on Android; we use 4. On iOS 26 the system tab bar renders Liquid Glass (see §6, open question). |
| Data | TanStack Query (cache, retries, pull-to-refresh) + ported offline queue | Query keys are per endpoint, and the cache is persisted to SQLite for a warm start. |
| Storage | `expo-sqlite` for the queue, cache and pending uploads; `expo-secure-store` for tokens [S11] | SQLite is encrypted (SQLCipher option) with a key kept in Keychain / Keystore. |
| Camera / scan | `expo-camera` `CameraView` barcode scanning; `launchScanner()` as a fallback [S5] | VisionCamera only if we need a frame processor later. |
| Biometrics | `expo-local-authentication` [S11] | Face ID / fingerprint app lock (§10). |
| Push | `expo-notifications` + Expo push service for alerts; direct APNs for Live Activity updates (M2) [S9] | See open question Q1. |
| Widgets / Live Activities | **`expo-widgets`** (stable since SDK 56; Android widgets opt-in in SDK 58) [S6][S7] | Widgets are written in JSX with `@expo/ui` SwiftUI / Compose components. They run in an isolated runtime with no hooks or async, so the app writes their data. |
| App Intents / Shortcuts | **`expo-app-intents` (alpha)** [S8] | Intent types are Swift; JS handles them. Pinned version, behind a feature flag (§7). |
| NFC | `react-native-nfc-manager` 4.x (config plugin) [S10] | M2. |
| Tap to Pay | `@stripe/stripe-terminal-react-native` (public preview, 0.0.1-beta.33) [S12] | M3; see §7.4. |
| Crash/perf | Sentry React Native | Source maps uploaded from EAS. |
| Tests | Jest (`jest-expo`) + React Native Testing Library; **Maestro** flows for E2E | The ported `lib/` tests run unchanged under Jest. |

## 6. Design language

These are the owner's standing rules, applied to native. The council's designer set the values.

- **Theme:** **light by default**, regardless of the phone's setting. Dark is an explicit choice in More (Light / Dark / System). Dark uses the web app's navy token values, never black.
- **Type:** Geist (bundled with `expo-font`, so nothing render-blocks). Five steps:

  | Token | Size / line | Use |
  |---|---|---|
  | `title` | 28/34 semibold | Large nav titles, the customer name on Job |
  | `heading` | 20/25 semibold | Section headers on Job, sheet titles |
  | `body` | 16/22 regular | Row titles, fields |
  | `subhead` | 14/19 regular | Row subtitles, device · problem |
  | `caption` | 12/16 regular | Due, captions, WO numbers |

  - Numbers use tabular figures.
  - **Geist Mono only for WO numbers, IMEI and serials.**
  - No uppercase labels, no wide tracking, nothing below 12 pt for readable text, and no weight above semibold except bold for the title.
  - Supports Dynamic Type up to XXL: rows grow, and nothing truncates the customer name before the device.
- **Surfaces:**
  - `canvas` (#f7f8fd) background;
  - **white inset grouped lists**, radius 12, 0.5 px hairlines (`line-200`);
  - **no shadows** except on sheets;
  - no cards inside cards.
- **Colour:**
  - **sapphire `#0059ba`** (`primary-fill`) only on interactive things (buttons, links, the selected tab);
  - status is a dot + name in lists, with a tinted pill only in the Job header, in the shop's own status colours;
  - **red only for late**;
  - balance shows in ink, not amber.
- **Motion:**
  - no-overshoot springs (~280 ms, damping set so there's no bounce);
  - press feedback is a highlight, never a scale;
  - native push/pop and sheet transitions;
  - list rows animate between sections;
  - reduced motion is respected.
  - No pulsing dots, glow, glass or clay.
- **Haptics (`expo-haptics`):**
  - selection tick: picking in sheets;
  - light impact: status change, scan decode;
  - success: check-in created, upload batch done, payment (M3);
  - warning: refusal or error.
- **Icons:** SF Symbols on iOS (via `expo-symbols`), Material Symbols on Android, in one weight.
- **Copy:** plain words ("2 days late", "Still owed"), sentence case, no filler, no emoji.

**Shared tokens.** Colours come from `frontend-react/theme.tokens.js` (the web app's live source of truth: `NEUTRALS` light/dark pairs plus the tint families). A script, `mobile/scripts/gen-tokens.mjs`, imports that file and writes `mobile/src/theme/tokens.generated.ts`, a plain `{ light, dark }` object of hex values. CI regenerates it and fails on a diff, so web and native never drift. The type scale, spacing (4-pt grid: 4/8/12/16/20/24/32) and radii (8/12/16) live in `mobile/src/theme/scale.ts`. They are native-only and are not pushed back into the web file.

## 7. OS surfaces, and when each lands

| Surface | What it does | Milestone | Requirements / risk |
|---|---|---|---|
| **Universal / app links** | `/go/j/{code}` opens the app, or the PWA when the app isn't installed | **M1** | Backend serves `/.well-known/apple-app-site-association` + `assetlinks.json` (missing today) |
| **Push** (alerts with actions) | "Mark ready" / "Open" actions on a job push | **M1** | §12 |
| **Home screen widgets** | Owner: the glance figures. Tech: my next 3 jobs. Desk: ready-for-pickup count. | **M2** | `expo-widgets` stable [S6]. The app writes a snapshot to the shared App Group and reloads the timeline after each sync. Interactive buttons need iOS 17+ [S7]. |
| **Lock Screen widgets** | Late count; my active timer | **M2** | Accessory families are supported [S7] |
| **Live Activities** | A running job timer for the tech, and "Check-in in progress" | **M2** | iOS only. Local start; remote start needs iOS 17.2+ [S7]. Server updates need direct APNs (`liveactivity` push type). |
| **App Shortcuts / Siri / Spotlight** | "Scan a label", "Find job…", "Check in a device", "Clock in" | **M2** (behind a flag) | `expo-app-intents` is **alpha** and may break [S8]. If it slips, fall back to a small Swift target. The PWA's deep links already work from the Shortcuts app. |
| **Share sheet** | "Add to Tekly job…" from Photos | **M2** | Share extension (config plugin) |
| **NFC tags** | Tap a tag on the bin or tray → job | **M2** | `react-native-nfc-manager` [S10]. iPhone XS+ reads NDEF in the background when the tag holds the universal link. |
| **Android widgets** | Same as iOS, a subset of components | **M2** | Opt-in `enableAndroid` [S7]; a smaller component set |
| **Tap to Pay** | Take a card on the phone | **M3** | §7.4 |

### 7.4 Tap to Pay (M3)
- **SDK:** `@stripe/stripe-terminal-react-native` supports Tap to Pay on iPhone and Android, with an Expo config plugin. It is labelled **public preview** (0.0.1-beta.33, Sep 2026) [S12].
- **iPhone:** XS or later, on an iOS release under a year old (not betas). PIN entry needs 16.4+. GA in US, CA, GB, IE, AU, NZ, most of the EU, CH and SG [S13].
- **Android:** 13+, built-in NFC, not rooted, a patch level under 12 months old, and Google Play services [S13].
- **Apple entitlement** (`com.apple.developer.proximity-reader.payment.acceptance`):
  - first a development entitlement, then a separate distribution entitlement after testing;
  - required before review: a "How to tap" education screen (`ProximityReaderDiscovery` on iOS 18+) and Apple's Tap to Pay HIG and marketing rules [S13].
  - **Apple publishes no SLA**, and reports range from days to about 2 months [S13a]. **We request the development entitlement at the start of M2**, so it doesn't block M3.
- **Our side:**
  - reuse the Stripe Connect account the shop already uses for payment links;
  - Location permission is mandatory [S12];
  - payments need a `can_take_payments` permission and a fresh Face ID check;
  - the PWA's `pay.ts` math (quick tender, clamp, finish target) ports as-is.

## 8. Offline and photo-upload reliability

- **Reads:** the TanStack Query cache is persisted to SQLite. Today, Find results and the last 50 opened jobs open offline, with an "Offline · updated 14:02" caption.
- **Writes** go through the ported **offline queue** (`offlineQueue.ts`), whose replay rules are already tested:

  | Server answer | What the queue does |
  |---|---|
  | 2xx | done |
  | 409 | conflict the user can re-apply or discard |
  | 401 | stop and ask to sign in; nothing is dropped |
  | 403 `network_not_allowed` | hold until the phone is back on shop Wi-Fi |
  | other 4xx | dropped and reported |
  | 5xx or network error | retry with backoff |

  - Port changes: the store becomes SQLite, not IndexedDB. Encryption becomes an injected cipher: SQLCipher encrypts the whole database with a Keychain-held key, so the queue's own AES layer is the identity function on native (the tests cover both).
  - Every queued write carries an **`Idempotency-Key`** (a client UUID), so a replay after a lost response never creates a duplicate job, photo or message.
- **Photos:**
  - The file is written to the app's documents folder first, together with a SQLite row: `{id (uuid), order_id, label, path, bytes, state: pending|uploading|uploaded|attached|failed, attempts, last_error}`.
  - The upload uses `expo-file-system` upload tasks; on iOS the default session type is `background`, so the transfer continues while the app is suspended [S15].
  - The JS task is **not** restored after the app is killed [S15], so on every launch, return to foreground and connectivity change the app re-drives the queue. An `expo-background-task` run does the same periodically, at the OS's discretion (about 15 min minimum) [U].
  - Each upload sends `Idempotency-Key = photo uuid`, then `intake-photos/add` with that media id (that call is already append-only and retry-safe on `450a9d2`).
  - The local file is deleted only after `attached`.
  - After 5 failures the row turns `failed` with a red badge on the job, plus a "Retry" action in More → Uploads. **Nothing is silently dropped.**
- **Conflicts:** a status change refused on replay shows on the job as "Couldn't change to Ready: signature needed", with *Fix* or *Discard*.

## 9. API contract: `/api/mobile/v1`

- **Prefix:** the app calls only `/api/mobile/v1/*`.
  - Where an existing endpoint already fits, the mobile router mounts it unchanged, so there is one implementation.
  - New aggregates (board, job, search, intake, glance) live in `backend/routers/mobile_v1/`.
  - The public partner API stays at `/api/v1`, a different audience.
- **Client header:** every request sends `X-Tekly-Client: go-ios/1.4.0+87` (platform/version+build). The device-sessions work already reads `X-Tekly-Client` to label devices.
- **Versioning rules:**
  - Within v1, changes are **additive only**: new fields and new endpoints. The app ignores unknown fields.
  - A breaking change means `/api/mobile/v2`, mounted side by side. v1 stays until no supported build uses it.
- **Force update:**
  - `GET /api/mobile/v1/config` (no auth) returns `{min_build:{ios,android}, latest_build:{…}, store_url:{…}, features:{…}}`.
  - Below `min_build`, any mobile endpoint answers **426** `{code:"client_too_old", store_url}`, and the app shows a full-screen "Update Tekly Go" screen.
  - Below `latest_build`, a dismissible banner is shown once a day.
  - `features` lets us switch off a surface (e.g. App Intents) without a release.
- **Types:**
  - FastAPI's OpenAPI for the mobile router → `openapi-typescript` → `mobile/src/api/schema.d.ts`.
  - CI regenerates it and fails on drift, the same pattern as the tokens.
  - A contract test in `backend/tests/test_mobile_v1_contract.py` snapshots the response keys of every mobile endpoint.
- **Errors:** `{code, message}` with stable `code`s (`consent_required`, `network_not_allowed`, `client_too_old`, `gate_failed`, `idempotency_conflict`). The app shows `message`; the logic switches on `code`.

## 10. Auth

- **Sign-in:** email + password, then a 2-step code when `X-MFA-Required` comes back (reusing the existing flow). There is no signup or password reset in the app; those link to the web.
- **Tokens:** `POST /api/mobile/v1/auth/token` returns:
  - a **short access token (15 min)**;
  - an **opaque refresh token** (60 days, **rotating**, bound to one `auth_sessions` row).
  - **Reuse detection:** a used refresh token presented again revokes that session.
  - Tokens live in `expo-secure-store`, access token in memory.
  - Today's web app has no separate refresh token (one 24 h JWT that slides for up to 30 days); the mobile flow adds one without changing the web.
- **Per-device sessions:**
  - each install is one session row (device name, platform, build, last seen, IP);
  - More → Devices shows them and can sign out others;
  - the web app's Team → person → Devices lets an owner revoke any phone;
  - revoking a session also deletes that device's push registration.
  - This builds on the **device-sessions work, which is not committed to any branch yet** (prerequisite B0, §13).
- **Face ID / fingerprint lock:**
  - optional and off by default; when on, the app locks after 10 min in the background (the ported `idle.ts` timings);
  - biometric failure falls back to the device passcode, never to a Tekly password;
  - the **passcode reveal** and (M3) payments always ask for biometrics.
  - The PWA's payment PIN is not carried over in v1.
- **Shop-only access:** when the owner turns it on, a 403 `network_not_allowed` shows "Connect to the shop Wi-Fi", and queued writes are held, not dropped.

## 11. App Store and Play rules

- **No in-app purchase, no prices, no signup, no "upgrade" links.** Tekly Go is a free, login-only companion to a business subscription sold on the web.
  - 3.1.3(f) covers "free stand-alone companion" apps but lists only VoIP, cloud storage, email and web hosting [S16].
  - The stronger fit is **3.1.3(c) Enterprise Services**: an app "sold directly by you to organizations… for their employees" [S16].
  - The app has nothing to buy, and shows no calls to action, so we don't depend on the US-storefront exception in 3.1.1(a) [S16].
  - Review notes will say this, and we'll provide a demo shop login.
- **Account deletion:**
  - 5.1.1(v) applies "if your app supports account creation" [S16], and ours doesn't.
  - Google Play wants an in-app path and a web link when an app "enables account creation" [S17].
  - **We ship More → Delete account anyway** (cheap insurance against rejection):
    - for staff, it sends a deletion request that the owner confirms, or that is auto-deactivated after 14 days;
    - for owners, it links to the web's close-account flow;
    - a public web page gives the same request route for Play's Data safety form.
- **OTA updates (EAS Update):**
  - used only for bug fixes and copy, never to add features that change what review saw (Guideline 2.5.2 [S16]);
  - anything touching native modules goes through the store.
- **Tap to Pay:** the entitlement lead time, the "How to tap" screen and the marketing rules are in §7.4.
- **Privacy:**
  - camera, photos, location (M3 only), NFC (M2), notifications and Face ID each get a plain-words purpose string;
  - privacy nutrition labels: contact info (customers'), photos, identifiers and diagnostics, none used for tracking;
  - no ATT prompt, since we do no tracking.
- **Developer accounts (lead time):**
  - Apple Developer **Organization** enrollment needs a D-U-N-S number;
  - Google Play needs a verified organisation account;
  - start both during M0, because they can take days to weeks.

## 12. Push (APNs / FCM)

- **Registration:**
  - after sign-in and an explicit "Turn on notifications" step (never a cold prompt), the app gets an Expo push token plus the native device token;
  - it calls `POST /api/mobile/v1/devices/push` with `{session_id, platform, expo_token, native_token, build, tz, locale}`;
  - it re-registers on every launch, which is cheap and idempotent.
- **Sending:** the existing `services/push.notify(kind, …)` gains a native fan-out next to web push.
  - v1 sends through the **Expo push service** (free; 600/s per project [S9]). It hides APNs/FCM HTTP v1 credentials behind EAS [S9b], and its receipts tell us which tokens are dead so we can prune them.
  - **Live Activity updates (M2)** go **directly to APNs** with a token-based `.p8` key, because they need the `liveactivity` push type [U: Expo's push service support for this type isn't confirmed].
- **Content:**
  - the title is the customer + device, the body is what changed;
  - **no customer phone numbers or message bodies in the payload**;
  - `data.url` is the deep link;
  - iOS uses `threadIdentifier` per job [S2a];
  - Android uses one channel per kind (Jobs, Messages, Approvals, Daily summary) so users can mute in system settings.
- **Kinds:**
  - existing: `job.assigned`, `schedule.changed`, `chat.message`, `estimate.approved`, `payment.received`;
  - new: `job.overdue`, `approval.needed`, `refund.requested`, `part.arrived`, `daily.summary`.
  - Per-kind preferences reuse `/api/tech/preferences`, with role-based defaults.

## 13. Backend gap list

Checked against `launch/audit-fixes` (LAF, the integration branch) and the Go PWA branch `450a9d2` (GO). `main` is 158 commits behind LAF.

**Prerequisites (must land on the integration branch before M1 work):**

| # | What | Where it is now |
|---|---|---|
| B0a | Go PWA backend: Idempotency-Key service (`services/idempotency.py`, `idempotency_records`), `intake-photos/add` + label, `/api/mobile/*` messaging (compose / handoff / consent), `OrderIn.due_at`, shop-timezone tech dashboard, `can_close_tickets`, drop-off terms + signature | **Committed only on `450a9d2` / `go/pay-reauth`** (PR #10, unmerged) |
| B0b | Per-device sessions: `auth_sessions` table (`ds144a1`), `/api/auth/sessions*`, admin sign-out, `X-Tekly-Client` labelling | **Uncommitted** in another session's scratchpad. It must be committed and merged, otherwise refresh tokens have nothing to bind to. |

**New or changed endpoints** (all under `/api/mobile/v1` unless noted):

| # | Endpoint | Status today | What's missing |
|---|---|---|---|
| B1 | `GET /config` | MISSING | `{min_build, latest_build, store_url, features}`; plus a 426 `client_too_old` dependency on all mobile routes |
| B2 | `POST /auth/token`, `POST /auth/refresh`, `POST /auth/logout` | PARTIAL: `/api/auth/token` + sliding `/refresh` of the access JWT; no refresh token | A 15-min access token + a rotating opaque refresh token bound to an `auth_sessions` row, with reuse detection. The MFA (`X-MFA-Required`) and password-reset (`X-Password-Reset-Required`) headers are unchanged. |
| B3 | `GET /board?scope=mine\|shop` | PARTIAL: `/api/tech/dashboard` cards lack `created_at`/age, balance and status colour and are assigned-to-me only; `/api/orders/board` lacks complaint/due/age (uncommitted WIP adds them) and computes `overdue` with `utcnow` against a shop-local `due_at` (bug) | Row: `id, order_number, barcode, status{key,name,color,group}, client{id,name,phone_last4}, device{kind,label}, complaint (≤80 chars), due_at, is_late, created_at, age_days, assignee{id,name,initials}, balance_due, priority, updated_at`. Sections computed **in shop timezone** (`late, due_today, in_progress, waiting, ready`). ETag / `If-None-Match`. |
| B4 | `GET /jobs/{id}` | PARTIAL: order detail exists. `/api/statuses/allowed/{id}` returns **keys only** and mixes estimate and work-order keys. | One aggregate: header, `allowed_statuses[{key,name,color,group,needs:[comment\|checklist\|payment\|signature]}]` filtered by record type, `suggested_next`, `watch_outs{passcode_set, flagged_notes[], preferred_channel}`, photos with labels, charges + **`balance_due`** (today only in `invoice-preview`), signature, last 5 history items |
| B5 | `PUT /jobs/{id}/device/passcode`, `POST /jobs/{id}/device/passcode/reveal` | MISSING: `Device.passcode_enc` column exists but nothing writes it | Write the encrypted value; reveal returns plaintext once and writes an audit row (who, when, device) |
| B6 | `GET /search?q=` | PARTIAL: `/api/orders?search=` uses a substring on the raw phone string, so "last 4" fails on formatted numbers | Digit-normalised phone match (a stored `phone_digits` column + index), WO number, name, IMEI/serial; returns B3 rows, limit 20, ranked exact WO > phone > name |
| B7 | `POST /intake` | MISSING: needs `POST /api/clients` then `POST /api/orders` (asset inline); passcode not writable | One idempotent call: `{client:{id}\|{name,phone}, device{kind,brand,model,imei,serial,color,condition}, complaint, passcode?, due_at?, quote_amount?, intake_photo_ids[], signature_media_id?, consent{sms,whatsapp}}` → `{order (B4 shape), label_url, tracking_url}`. Client deduplicated by E.164. |
| B8 | `GET /jobs/{id}/label.pdf?template=` | MISSING: order stickers render as **HTML only** (`/api/documents/render`); `POST /api/print/labels` is products-only | A PDF at the template's label size with the QR = `https://<host>/go/j/{barcode}`, for AirPrint / Android print. ZPL for thermal printers is deferred. |
| B9 | `POST /uploads` | PARTIAL: `POST /api/media` takes **base64 JSON** only, has no idempotency, 8 MB cap | Multipart binary upload with `Idempotency-Key`. The `intake-photos/add` attach (GO) is reused. (A presigned S3 PUT is optional later.) |
| B10 | `POST /devices/push`, `DELETE /devices/push/{id}` | MISSING: only the web-push `PushSubscription` exists | New table `push_devices {id, org_id, employee_id, session_id→auth_sessions, platform, provider, expo_token, native_token, build, tz, locale, last_seen_at, revoked_at}`. `services/push.notify` fans out to it via the Expo push API; dead tokens are pruned from receipts; revoking a session cascades. |
| B11 | New push kinds | MISSING | `job.overdue` (scheduled sweep every 15 min, shop tz, once per job), `approval.needed` (an owner-set amount threshold), `refund.requested`, `part.arrived`, `daily.summary` |
| B12 | `GET /glance` | PARTIAL: `/api/reports/company-dashboard` uses the **UTC** day and has no promised-not-ready count | `{promised_not_ready, overdue, ready_for_pickup, revenue_today, currency, as_of}` in shop tz. Small and cacheable (60 s), also feeds the widget. |
| B13 | `/.well-known/apple-app-site-association`, `/.well-known/assetlinks.json` (root, not under `/api`) | MISSING: the SPA catch-all in `main.py` would swallow them | Explicit routes serving JSON, with the app IDs from config. The `/go/j/{code}` route resolves to the job (PWA fallback). |
| B14 | `POST /account/deletion-request` (+ public web page) | MISSING: only admin deactivation (`DELETE /api/staff/employees/{id}`) | The staff request notifies the owner and auto-deactivates after 14 days unless cancelled; for owners it returns the web close-account URL |
| B15 | `POST /devices/live-activity` (M2) | MISSING | Stores `{activity_id, push_token, order_id}`; the status-change / timer hooks send APNs `liveactivity` updates |
| B16 | Messaging, timer, scan, status | EXISTS (GO / LAF) | Mounted under the mobile prefix unchanged: `/api/mobile/orders/{id}/compose`, `/messages`, `/api/timeclock/start\|stop\|active`, `/api/scan/resolve`, `PATCH /api/orders/{id}/status` (+ `Idempotency-Key`) |

Small fixes found on the way (not mobile-only): the `overdue` timezone bug in `/api/orders/board` (`orders.py:1114`), and the company dashboard "today" being the UTC day (`reports.py:978`).

## 14. Repo layout

The app lives at **`mobile/`** at the repo root, beside `backend/`, `frontend-react/` and `website/`.

- It is a standalone npm project with its own `package-lock.json`. There are no workspaces, so the web build is untouched.
- It reads two things from the rest of the repo, both at build time through scripts with CI drift checks:
  - `frontend-react/theme.tokens.js`, for colours;
  - the backend OpenAPI, for types.

```
mobile/
  app.config.ts            # name, bundle ids, plugins, runtimeVersion: fingerprint
  eas.json                 # development / preview / production profiles + channels
  package.json  tsconfig.json  jest.config.js  .maestro/
  app/                     # Expo Router
    _layout.tsx            # providers, fonts, theme, auth gate, force-update gate
    sign-in.tsx
    (tabs)/_layout.tsx     # NativeTabs: today, find, scan, more
    (tabs)/today.tsx  (tabs)/find.tsx  (tabs)/scan.tsx  (tabs)/more/…
    job/[id]/index.tsx  job/[id]/photos.tsx  job/[id]/history.tsx
    check-in/_layout.tsx  check-in/{customer,device,problem,sign}.tsx
    go/j/[code].tsx        # universal-link landing → routeScan
  src/
    api/  client.ts  schema.d.ts (generated)  hooks/*.ts
    lib/  (ported from the PWA, see §15)
    storage/ db.ts  queueStore.ts  uploads.ts  secure.ts
    theme/ tokens.generated.ts  scale.ts  index.ts  useTheme.ts
    ui/    Text  GroupedList  Row  Button  StatusDot  StatusPill  Sheet  Avatar  Field  Banner
    features/ today  job  scan  find  checkin  push  glance
  widgets/                 # M2: expo-widgets JSX
  targets/                 # M2+: Swift for App Intents if the alpha module slips
  scripts/ gen-tokens.mjs  gen-api-types.mjs
```

## 15. What ports from the PWA's `lib/`

Source: `frontend-react/src/go/lib/` on `450a9d2`. "As-is" means the file is copied and its test passes unchanged.

| Module | Port | Why / change |
|---|---|---|
| `scanRoute.ts` (+ test) | **As-is** | Pure. Add the `/go/j/{code}` direct-link pattern. |
| `statusChange.ts` (+ test) | **As-is**, one import | Replace `readableError` from `../../api/client` with `src/lib/errors.ts` |
| `offlineQueue.ts` (+ test) | **Small change** | Store injected already; inject the cipher instead of `SubtleCrypto` (identity on native, AES in tests), plus an `Idempotency-Key` per item |
| `queue.ts` (sort / merge) | **As-is** | Pure |
| `idle.ts` (+ test) | **As-is** | Pure timings; storage via an injected getter |
| `messaging.ts` | **As-is** | `smsLink` / `waLink` / `smsSegments`; `isIOS` → `Platform.OS` |
| `newJob.ts` | **As-is** | `splitNamePhone`, `duePresets`, `missingFields`, `addProblem` feed check-in |
| `time.ts` | **As-is** | Pure |
| `pay.ts` | **As-is, in M3** | Tender / change / finish-target math |
| `photos.ts` | **Logic only** | Keep `fitWithin`, `PHOTO_LABELS`; resizing moves to `expo-image-manipulator` |
| `role.ts` | **Rewrite (small)** | Drop the saved picker; derive role from `/me` permissions |
| `push.ts`, `scanner.ts`, `passkey.ts`, `ticket.ts`, `pin.ts`, `useJobTimer.ts`, `useGo.ts` | **Not ported** | Web-only APIs (web push, ZXing on canvas, WebAuthn, iframe print) or cut from v1 (PIN). Native equivalents are written fresh. |

## 16. CI, EAS builds and OTA updates

- **GitHub Actions:** a new `mobile` job in `.github/workflows/ci.yml`, path-filtered to `mobile/**`, `frontend-react/theme.tokens.js` and `backend/routers/mobile_v1/**`. It runs on Node 22, `npm ci`, then:
  - `tsc --noEmit`;
  - ESLint;
  - the Jest unit tests with a coverage floor of 70% on `src/lib`;
  - `gen-tokens --check`;
  - `gen-api-types --check`;
  - `npx expo-doctor`.
- **Backend:** the mobile contract tests run in the existing `test` job.
- **EAS Build profiles:**
  - `development` (dev client, simulator + internal devices);
  - `preview` (internal distribution / TestFlight internal, `preview` channel);
  - `production` (store, `production` channel).
- **EAS Workflows:** on merge to `main`, build `preview` for both platforms and run **Maestro** E2E flows (sign-in, scan → job, status change, check-in) on the EAS simulator and emulator.
- **Store submits** are manual, through `eas submit` after a release checklist.
- **OTA (EAS Update):**
  - `runtimeVersion: { policy: "fingerprint" }` [S18], so an update only reaches builds with matching native code;
  - channels mirror the profiles;
  - each update is staged at 10% → 100% with Sentry crash checks between;
  - bug fixes and copy only (§11).
- **Versioning:** the marketing version comes from `app.config.ts`; the build number is auto-incremented remotely by EAS. `min_build` (§9) is raised by hand only when the API needs it.

## 17. Metrics per phase

| Phase | Metric | Target |
|---|---|---|
| Pilot (PWA, now) | Share of status changes and photos done on the phone at the pilot shop; how the counter looks jobs up | Decides the M1 ordering |
| M1 | Scan → job open (p50, warm) | < 2 s |
| M1 | Status changes made on the phone / all status changes, per active shop | ≥ 40% by week 4 |
| M1 | Photos per job (jobs with ≥ 1 phone photo) | ≥ 50% of jobs |
| M1 | Check-in time, open → label printed (p50) | < 60 s |
| M1 | Find → job open (p50) | < 3 s |
| M1 | Queued writes replayed successfully within 10 min of reconnecting | ≥ 99% |
| M1 | Photo uploads reaching `attached` within 24 h | ≥ 99.9% (0 silent losses) |
| M1 | Crash-free sessions (Sentry) | ≥ 99.5% |
| M1 | Weekly active staff / staff with the app installed | ≥ 60% |
| M2 | Shops with ≥ 1 widget installed; App Shortcut invocations / week | Baseline, then +10% per month |
| M2 | Owner push open rate; owner weekly app opens | ≥ 30%; ≥ 3 / week |
| M3 | Share of in-shop card payments taken by Tap to Pay at shops that enable it | ≥ 25% after 4 weeks |
| M3 | Tap to Pay authorisation success | ≥ 97% |

Events are sent to our own backend (`POST /api/mobile/v1/events`, batched, no third-party analytics SDK) and joined with server data for the "share of" metrics.

## 18. Open decisions

These are asked one at a time, each with a recommendation.

1. **Push provider.** *Recommend:* the Expo push service for alerts, with direct APNs only for Live Activities. *Alternative:* direct APNs + FCM from day one: no third party, but more credential handling on our server.
2. **In-app "Delete account".** *Recommend:* ship it even though the app creates no accounts.
3. **iOS 26 tab bar.** Native tabs render Apple's Liquid Glass. *Recommend:* accept it, because it's the system look, not our decoration, and fighting it makes the app feel non-native. *Alternative:* a custom flat tab bar (JS tabs).
4. **Where the app lives.** *Recommend:* `mobile/` as a standalone npm project.
5. **Branch base.** This branch was cut from `main`, 158 commits behind `launch/audit-fixes`. *Recommend:* merge PR #10's backend (B0a) and commit the device-sessions work (B0b) into the integration branch before M1.
6. **Developer accounts.** Who enrols the Apple organisation account (needs a D-U-N-S number) and the Play organisation account, and under which legal name. *Recommend:* start during M0.
7. **App name and identifiers.** *Recommend:* "Tekly Go", `com.tekly.go` on both stores.

## 19. Sources (checked 2026-10-23)

- [S1] Expo changelog (SDK 58 released 2026-10-15; SDK 57 Jun 30; SDK 56 May 21): https://expo.dev/changelog
- [S2] Expo SDK 58 reference (RN 0.88, React 19.3, iOS 16.4+, Android 7+, targetSdk 36, Xcode 26.4+): https://docs.expo.dev/versions/latest/ and https://expo.dev/changelog/sdk-58
- [S2a] SDK 58 notifications changes (`threadIdentifier`, foreground default): https://expo.dev/changelog/sdk-58
- [S3] SDK 55: Legacy Architecture removed: https://expo.dev/changelog/sdk-55
- [S4] Expo Router native tabs (stable in SDK 58, `expo-router/native-tabs`): https://docs.expo.dev/router/advanced/native-tabs/
- [S5] expo-camera barcode scanning and `launchScanner()`: https://docs.expo.dev/versions/latest/sdk/camera/
- [S6] expo-widgets stable in SDK 56: https://expo.dev/changelog/sdk-56 and https://expo.dev/blog/home-screen-widgets-and-live-activities-in-expo
- [S7] expo-widgets SDK 58 (Lock Screen, Live Activities, Android opt-in, iOS 17 interactive): https://docs.expo.dev/versions/v58.0.0/sdk/widgets.md
- [S8] expo-app-intents (alpha, "will frequently experience breaking changes"): https://docs.expo.dev/versions/latest/sdk/app-intents/
- [S9] Expo push FAQ (free, 600/s per project): https://docs.expo.dev/push-notifications/faq/
- [S9b] FCM HTTP v1 credentials on EAS: https://docs.expo.dev/push-notifications/fcm-credentials/
- [S10] react-native-nfc-manager (config plugin, New Architecture): https://github.com/revtel/react-native-nfc-manager
- [S11] expo-secure-store / expo-local-authentication (SDK 58): https://expo.dev/changelog/sdk-58
- [S12] Stripe Terminal React Native setup (public preview, Expo plugin, location required): https://docs.stripe.com/terminal/payments/setup-integration?terminal-sdk-platform=react-native and https://github.com/stripe/stripe-terminal-react-native
- [S13] Stripe Tap to Pay (devices, countries, Apple entitlement steps): https://docs.stripe.com/terminal/payments/setup-reader/tap-to-pay?platform=ios and `?platform=android`; Apple marketing guidelines: https://developer.apple.com/tap-to-pay/marketing-guidelines/
- [S13a] Entitlement wait reports (no Apple SLA): https://developer.apple.com/forums/thread/742310
- [S14] Google Play target API (API 36 from 2026-08-31): https://developer.android.com/google/play/requirements/target-sdk
- [S15] expo-file-system upload tasks (background session on iOS; not restored after kill): https://docs.expo.dev/versions/latest/sdk/filesystem/
- [S16] App Store Review Guidelines 3.1.1(a), 3.1.3(c)/(f), 5.1.1(v), 2.5.2: https://developer.apple.com/app-store/review/guidelines/
- [S17] Google Play account deletion requirement: https://support.google.com/googleplay/android-developer/answer/13327111
- [S18] EAS Update runtime versions (fingerprint policy): https://docs.expo.dev/eas-update/runtime-versions/
- [U] Unverified, to be re-checked when building:
  - Expo push support for the `liveactivity` push type;
  - `expo-background-task` minimum interval;
  - the current Apple licence-agreement clause on interpreted code;
  - Apple's official Tap to Pay entitlement turnaround.
