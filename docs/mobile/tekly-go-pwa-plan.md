# Tekly Go (browser shortcut app): implementation plan

Status: **draft for owner approval. Nothing here is started.** Date: 2026-10-23.
Spec: [`tekly-go-pwa-spec.md`](tekly-go-pwa-spec.md). Section references (§n) point there. The native app plan is kept for later in [`tekly-go-native-plan.md`](tekly-go-native-plan.md).

## How to use this plan

- **Gate:** no app code until the one-shop QR pilot reports. The pilot picks the M1 order. M0 can start when the pilot ends.
- **Every task is 2–5 minutes**, TDD:
  1. write the test in *Test*;
  2. run it and see it **fail**;
  3. make the change;
  4. run *Verify* and see it **pass**;
  5. commit with the task ID (e.g. `GO-W-S2.3: …`).
- **Commands** (from the repo root):
  - Go unit test: `cd frontend-react && npx vitest run src/go/<path> -t "<name>"`
  - Typecheck: `cd frontend-react && npx tsc --noEmit`
  - Go E2E: `cd frontend-react && npx playwright test e2e/go/<flow>.spec.ts --project=go-iphone` (and `--project=go-android`)
  - Backend: `pytest -q backend/tests/<file>::<test>`
- **Code locations:**
  - Go UI: `frontend-react/src/go/` (`ui/` kit, `data/` queries, `features/`, `screens/`, `lib/`);
  - backend Go aggregates: `backend/routers/go.py` (added to `ROUTERS` in `main.py`);
  - migrations: `backend/migrations/versions/`;
  - backend tests use the `client` / `tenant` / `db` fixtures.
- **Base:** the integration branch `launch/audit-fixes` **after** B0 lands, plus the Go PR stack (#10 → #11 → quick-fixes `3a681f0`).
- **Device check ✋** marks a step that has to be done by hand on a real iPhone or Android phone.

## B0: Prerequisites (owner-approved; running in their own sessions)

| ID | What | Done when |
|---|---|---|
| B0.1 | Go backend from PR #10/#11 on `launch/audit-fixes` | PR merged; `git grep -n idempotency_records -- backend` finds it |
| B0.2 | Per-device sessions committed and merged | PR merged; `git grep -n auth_sessions -- backend/migrations` finds it |
| B0.3 | Go frontend stack (#10 → #11 → quick-fixes → pilot kit) merged into `launch/audit-fixes` | `/go/today` serves the light quick-fixes look from the integration branch |

---

## M0: Foundations (≈ 4 days)

### M0.1 Kit (each: test first, then the component)
- **M0.1.1 Scale.**
  - File: `src/go/ui/scale.ts`, holding the five type steps, spacing, radii, hairline and easing.
  - Test: `src/go/ui/scale.test.ts` checks the exact pairs 28/34 … 12/16, and that none is below 12.
  - Verify: vitest passes.
- **M0.1.2 `Text`.**
  - File: `src/go/ui/Text.tsx`: `variant` title|heading|body|subhead|caption, `mono`, `tone`.
  - Test: `caption` → `text-[12px] leading-[16px]`; `mono` → `font-mono`; numbers get `tabular-nums`.
  - Verify: vitest passes.
- **M0.1.3 `GroupedList` + `Row`.**
  - File: `src/go/ui/GroupedList.tsx`.
  - Test: 0.5 px separators except after the last row; the whole row is one button; the pressed class is a background change, not `scale-`.
  - Verify: vitest passes.
- **M0.1.4 `Button`.**
  - File: `src/go/ui/Button.tsx`: primary (sapphire fill) or plain, min-h 44 px.
  - Test: the primary has `bg-primary`, and there is no gradient or shadow class.
  - Verify: vitest passes.
- **M0.1.5 `StatusDot` / `StatusPill`.**
  - File: `src/go/ui/Status.tsx`.
  - Test: it uses the shop's colour + name, and the pill uses `lib/tint.ts`.
  - Verify: vitest passes.
- **M0.1.6 `Avatar`.**
  - Do: re-export from `pages/orders/look.tsx` with a size prop.
  - Test: "Ana Lee" → "AL".
  - Verify: vitest passes.
- **M0.1.7 `Sheet`.**
  - File: `src/go/ui/Sheet.tsx`: a bottom sheet with a focus trap, Esc / drag-down closes it, and a shadow only here.
  - Test: it is open → closes on Esc and returns focus.
  - Verify: vitest passes.
- **M0.1.8 `Field`.**
  - File: `src/go/ui/Field.tsx`: a floating label (like `public/authUi.tsx`) and an error line.
  - Test: the error is announced (`aria-live="polite"`).
  - Verify: vitest passes.
- **M0.1.9 `Banner`.**
  - Test: offline → "Offline · updated 14:02"; update → "New version ready · Reload".
  - Verify: vitest passes.
- **M0.1.10 `TabBar`.**
  - File: `src/go/ui/TabBar.tsx`: flat white, hairline top, 4 tabs, safe-area padding.
  - Test: 4 labels; hidden when `useMatch("/check-in/*")` matches.
  - Verify: vitest passes.
- **M0.1.11 `NavBar` with a large title and a right action slot.**
  - Test: it collapses to a small title after 40 px of scroll (`IntersectionObserver` mocked).
  - Verify: vitest passes.
- **M0.1.12 `ActionBar`.**
  - Test: it renders docked above the tab bar, and it sets the CSS var `--go-actionbar-h` used as the list's bottom padding.
  - Verify: vitest passes.
- **M0.1.13 `PullToRefresh`.**
  - Test: pulling past 64 px calls `onRefresh` once; a smaller pull does not.
  - Verify: vitest passes.
- **M0.1.14 `haptics`.**
  - File: `src/go/ui/haptics.ts`: `navigator.vibrate` when present, a no-op otherwise.
  - Test: iOS (no vibrate) doesn't throw.
  - Verify: vitest passes.
- **M0.1.15 Lint guard.**
  - File: `frontend-react/scripts/check-go-ui.mjs`, which fails on `text-[` arbitrary sizes, raw hex, `scale-` press, `shadow-` (outside Sheet), `backdrop-blur` and `animate-pulse` under `src/go/screens|features`.
  - Test: a fixture file with `text-[13px]` fails.
  - Verify: `node scripts/check-go-ui.mjs` exits 0 on the kit; add it to `npm run lint:uitext`.

### M0.2 Data layer
- **M0.2.1 Add `@tanstack/react-query` + `@tanstack/query-async-storage-persister` + `idb-keyval`.**
  - Verify: `npm ci && npx tsc --noEmit`.
- **M0.2.2 Query client.**
  - File: `src/go/data/queryClient.ts` (staleTime 30 s, persisted to IndexedDB, buster = build id).
  - Test: a persisted query rehydrates; a new build id drops it.
  - Verify: vitest passes.
- **M0.2.3 Online wiring.**
  - File: `src/go/data/online.ts`, connecting `online`/`offline`/`visibilitychange` to the Query `onlineManager` and to the queue flush.
  - Test: coming online calls `goQueue.flush()` once.
  - Verify: vitest passes.
- **M0.2.4 Idempotency key on queued writes.**
  - File: `src/go/lib/offlineQueue.ts`.
  - Test (in `offlineQueue.test.ts`): each item stores an `Idempotency-Key` UUID, and the replay sends the same key on every retry.
  - Verify: vitest passes.
- **M0.2.5 No remount flash.**
  - Do: convert `useGo` reads for Today and Job to `useQuery`.
  - Test: switching tabs and back renders the cached list with no skeleton.
  - Verify: vitest passes.

### M0.3 Shell polish
- **M0.3.1 Self-host Geist.**
  - Files: `frontend-react/public/fonts/Geist-*.woff2`, `GeistMono-Regular.woff2`; `@font-face` in `src/go/go.css`; remove the Google Fonts link for Go.
  - Test: `theme.test.ts` asserts that no `fonts.googleapis.com` link is in the Go HTML.
  - Verify: vitest passes; Lighthouse shows no render-blocking font.
- **M0.3.2 Light by default.**
  - Test: with no saved choice and `prefers-color-scheme: dark`, `document.documentElement` has no `dark` class.
  - Verify: vitest passes (the quick-fixes branch may already pass; keep the test).
- **M0.3.3 View transitions.**
  - File: `src/go/ui/navigate.ts` wraps router navigation in `document.startViewTransition` when it's available and reduced motion is off.
  - Test: with `startViewTransition` mocked it is called; with reduced motion it is not.
  - Verify: vitest passes.
- **M0.3.4 Cut v1 items:** remove the Tasks tab, Clock tab (timer moves to Job in S5; shift clock-in moves to More), the role picker and the Estimates toggle; "+" moves into the headers.
  - Test: `GoApp.test.tsx`: the routes `/tasks` and `/new/estimate` redirect to `/today`; the tab bar has exactly Today, Find, Scan, More.
  - Verify: vitest passes.
- **M0.3.5 Service-worker update banner.**
  - Files: `public/go/sw.js` (message `SKIP_WAITING` exists) and `src/go/data/swUpdate.ts`.
  - Test: a waiting worker shows the banner, and Reload posts `SKIP_WAITING`, then reloads.
  - Verify: vitest passes.
- **M0.3.6 Storage persist.**
  - Test: after the first sign-in in standalone mode, `navigator.storage.persist()` is called once.
  - Verify: vitest passes.

### M0.4 Install
- **M0.4.1 Backend: install links (B10), migration + model.**
  - File: `<rev>_go_install_links.py` (`code_hash`, `employee_id`, `expires_at`, `used_at`).
  - Test: `backend/tests/test_go_install.py::test_table`.
  - Verify: alembic upgrade, then pytest.
- **M0.4.2 `POST /api/go/install-links`.**
  - File: `backend/routers/go.py`.
  - Test: `test_create_link_for_self`: it returns a URL with a code, 10-min expiry.
  - Verify: pytest passes.
- **M0.4.3 Redeem.**
  - Test: `test_redeem_once_creates_device_session`, `test_redeem_twice_401`, `test_expired_401`.
  - Verify: pytest passes.
- **M0.4.4 Web: Settings → Tekly Go.**
  - File: `frontend-react/src/pages/settings/GoSettings.tsx`, showing the QR for the install link (existing QR helper), "Expires in 10:00" and "Make another".
  - Test: `GoSettings.test.tsx`.
  - Verify: vitest passes.
- **M0.4.5 Install page.**
  - File: `src/go/install/Install.tsx` at `/go/install?c=…`. It redeems the code, then shows iPhone steps (Share → Add to Home Screen, with a picture of the Share icon) or the Android **Install** button (`beforeinstallprompt`). It already shows "Installed ✓" when it is running standalone.
  - Test: the UA is iPhone → the iPhone steps; the Android event → the button; standalone → it redirects to `/go/today`.
  - Verify: vitest passes.
- **M0.4.6 Manifest refresh.**
  - File: `public/go/manifest.webmanifest`: `start_url` `/go/today`, `theme_color` matches the canvas, shortcuts = Scan, Find, Check in (Android).
  - Test: `manifest.test.ts` parses the JSON and asserts the 3 shortcuts.
  - Verify: vitest passes.
- **M0.4.7 Device check ✋.** On an iPhone (current iOS) and an Android phone: scan the QR → install → open from the icon. It arrives signed in. Note the time (goal < 1 min). Also test a **passkey** lock in iPhone standalone mode (spec §8, decides Q2).

### M0.5 CI
- **M0.5.1 Playwright Go projects.**
  - File: `frontend-react/playwright.config.ts`, adding `go-iphone` (`devices["iPhone 15"]`, WebKit) and `go-android` (`devices["Pixel 7"]`), with `testMatch: /e2e\/go\//`.
  - Test: `e2e/go/smoke.spec.ts` opens `/go/today` and sees "Today".
  - Verify: `npx playwright test e2e/go --project=go-iphone` passes.
- **M0.5.2 Add the projects to `.github/workflows/e2e.yml`.**
  - Verify: the Actions run is green.

**M0 done:**
- the kit is in place and the lint guard is clean;
- tabs switch without a flash and the font doesn't block rendering;
- installing from the QR takes under a minute on both phones;
- CI runs the Go E2E on both viewports.

---

## M1: The core loop (≈ 3 weeks)

M1 is **one foundation (F) + eight self-contained slices**. Each slice has its own backend tasks, UI tasks and E2E flow and depends only on F, so the pilot just orders them. The orderings are at the end of this section.

### F: Go API base (≈ 1 day)
- **F1 Router.**
  - File: `backend/routers/go.py` (`APIRouter(prefix="/api/go")`), added to `ROUTERS` in `main.py`.
  - Test: `backend/tests/test_go_api.py::test_mounted`.
  - Verify: pytest passes.
- **F2 Client header + min build (B11).**
  - File: `backend/routers/go.py` dependency `require_go_build` with `GO_MIN_BUILD` in `backend/config.py`.
  - Test: `test_old_build_426` and `test_missing_header_ok`.
  - Verify: pytest passes.
- **F3 App reacts to 426.**
  - File: `src/go/data/api.ts` (wraps `api/client.ts`).
  - Test: a 426 triggers a one-time `location.reload()`, with no reload loop.
  - Verify: vitest passes.
- **F4 Contract snapshots.**
  - File: `backend/tests/test_go_contract.py` with `backend/tests/snapshots/go/*.json`.
  - Verify: pytest passes.
- **F5 Role derivation.**
  - File: `src/go/lib/role.ts` `roleFromMe`.
  - Test (in `role.test.ts`): owner, desk and tech rules, and the override wins.
  - Verify: vitest passes.

### S1: Today
- **S1.1 Board row (B1).**
  - File: `backend/routers/go.py` `GET /board`.
  - Test: `test_go_board.py::test_row_fields` checks all the B1 keys.
  - Verify: pytest passes.
- **S1.2 Shop-tz sections.**
  - Test: `test_sections_shop_tz` (Los Angeles shop, a job due 23:30 local → `due_today`).
  - Verify: pytest passes.
- **S1.3 Scope.**
  - Test: `test_scope_mine_vs_shop`; `shop` needs `can_view_all_orders`.
  - Verify: pytest passes.
- **S1.4 ETag.**
  - Test: `test_etag_304`.
  - Verify: pytest passes.
- **S1.5 Fix the `/api/orders/board` overdue tz bug.**
  - File: `backend/routers/orders.py`.
  - Test: `backend/tests/test_orders_board_overdue_tz.py`.
  - Verify: pytest passes.
- **S1.6 `useBoard`.**
  - File: `src/go/features/today/useBoard.ts`.
  - Test: role section order, and empty sections hidden.
  - Verify: vitest passes.
- **S1.7 `JobRow`.**
  - File: `src/go/features/today/JobRow.tsx`.
  - Test: name; "iPhone 14 Pro · Cracked screen"; mono WO number; "2 days late" red only when late.
  - Verify: vitest passes.
- **S1.8 Today screen.**
  - File: `src/go/screens/Today.tsx` (rebuilt on the kit).
  - Test: all empty → "Nothing due today. Scan a label or tap + to check one in." and no zero tiles; pull to refresh refetches.
  - Verify: vitest passes.
- **S1.9 Row move.**
  - Test: a status change moves the row with a view transition; with reduced motion it doesn't animate.
  - Verify: vitest passes.
- **S1.10 E2E.**
  - File: `e2e/go/today.spec.ts`: the seeded late job is at the top in red.
  - Verify: playwright passes on both projects.

### S2: Scan → Job
- **S2.1 Job aggregate (B2).**
  - File: `GET /api/go/jobs/{id}`.
  - Test: `test_go_jobs.py::test_shape`.
  - Verify: pytest passes.
- **S2.2 Allowed statuses** filtered by record type, with names, colours and `needs`.
  - Test: `test_allowed_excludes_estimate_keys`, `test_needs_signature`.
  - Verify: pytest passes.
- **S2.3 `balance_due`.**
  - Test: `test_balance_matches_invoice_preview`.
  - Verify: pytest passes.
- **S2.4 Scope.**
  - Test: `test_404_other_org`, `test_403_outside_scope`.
  - Verify: pytest passes.
- **S2.5 Decoder.**
  - Do: add `barcode-detector`; `src/go/lib/scanner.ts` uses native `BarcodeDetector` when present, else the ponyfill, with `prepareZXingModule` pointing at a self-hosted `/go/zxing_reader.wasm` (copied in `vite.config.ts`).
  - Test: in `scanner.test.ts`, with no native detector the ponyfill is chosen and the wasm URL is same-origin.
  - Verify: vitest passes.
- **S2.6 Offline wasm.**
  - File: `public/go/sw.js`, which precaches `/go/zxing_reader.wasm`.
  - Test: `sw.test.ts` (the SW unit harness) checks the precache list includes it.
  - Verify: vitest passes.
- **S2.7 One camera stream.**
  - File: `src/go/screens/Scan.tsx`, which keeps the `MediaStream` across re-renders and stops it only on leaving Scan.
  - Test: re-rendering doesn't call `getUserMedia` again.
  - Verify: vitest passes.
- **S2.8 Scan outcomes.**
  - Test: job → navigate; part/asset → sheet; no match → "No job for this code" + "Search instead" (prefilled). Decode fires `haptics.impact()`.
  - Verify: vitest passes.
- **S2.9 Wake lock.**
  - Test: Scan requests `navigator.wakeLock` when available and releases it on leave.
  - Verify: vitest passes.
- **S2.10 Job header.**
  - File: `src/go/features/job/JobHeader.tsx`.
  - Test: the title is the customer; the caption reads "WO-00412 · Booked Tue 14:05"; the pill; due in red only when late.
  - Verify: vitest passes.
- **S2.11 Job groups.**
  - Test: the balance is in ink (not amber); IMEI is mono; the watch-outs strip shows "Passcode set".
  - Verify: vitest passes.
- **S2.12 Docked action bar.**
  - Test: it shows `suggested_next`, and the list's bottom padding ≥ the bar height.
  - Verify: vitest passes.
- **S2.13 Job screen on the kit.**
  - File: `src/go/screens/Job.tsx`.
  - Test: it renders from the snapshot fixture.
  - Verify: vitest passes.
- **S2.14 E2E.**
  - File: `e2e/go/scan-to-job.spec.ts`, injecting a decoded code through the test hook `window.__goScan("WO-…")`.
  - Verify: playwright passes.
- **S2.15 Device check ✋.** On the iPhone: scan a printed label from the pilot kit; confirm there's no camera re-prompt when going back and forth; time it (< 2 s).

### S3: Status + notify
- **S3.1 Idempotent status PATCH.**
  - Test: `test_status_idempotent.py` (same key → one history row).
  - Verify: pytest passes.
- **S3.2 `has_template` on allowed statuses.**
  - Test: `test_has_template_flag`.
  - Verify: pytest passes.
- **S3.3 Status sheet.**
  - File: `src/go/features/job/StatusSheet.tsx`.
  - Test: the suggested status is first, with dots + names.
  - Verify: vitest passes.
- **S3.4 Optimistic change.**
  - File: `useChangeStatus.ts` (wraps `lib/statusChange.ts`).
  - Test: it updates the job and board caches; a 409 restores the old status and shows the reason.
  - Verify: vitest passes.
- **S3.5 Queued and held.**
  - Test: offline → "Waiting to sync"; held → "Will sync on shop Wi-Fi".
  - Verify: vitest passes.
- **S3.6 "Tell {name}?" chip.**
  - Test: it shows after a status with a template and hides after 6 s; a tap opens the Message sheet with that template.
  - Verify: vitest passes.
- **S3.7 Message sheet on the kit.**
  - File: `src/go/components/MessageSheet.tsx`.
  - Test: SMS opens `sms:+…&body=` on iOS; WhatsApp is hidden when consent is false.
  - Verify: vitest passes.
- **S3.8 "Sent?" on return.**
  - Test: `visibilitychange` → visible after opening the composer shows Sent / Not sent, and Sent PATCHes the handoff.
  - Verify: vitest passes.
- **S3.9 E2E.**
  - File: `e2e/go/status-notify.spec.ts`.
  - Verify: playwright passes.

### S4: Photos
- **S4.1 Multipart upload (B7).**
  - File: `POST /api/go/uploads`.
  - Test: `test_go_uploads.py::test_multipart_jpeg`.
  - Verify: pytest passes.
- **S4.2 Idempotent upload.**
  - Test: `test_same_key_same_media`.
  - Verify: pytest passes.
- **S4.3 Limits.**
  - Test: `test_rejects_non_image` (415) and `test_8mb_cap` (413).
  - Verify: pytest passes.
- **S4.4 Photo store.**
  - File: `src/go/features/photos/store.ts` (IndexedDB blobs + rows, state machine).
  - Test: transitions; `attempts` increments; the blob is deleted only at `attached`.
  - Verify: vitest passes.
- **S4.5 Resize.**
  - File: `src/go/lib/photos.ts` (keeps `fitWithin`; `shrinkPhoto` returns a Blob, not a data URL).
  - Test: 4032×3024 → 1600×1200.
  - Verify: vitest passes.
- **S4.6 Upload driver.**
  - File: `src/go/features/photos/driver.ts`.
  - Test: success → `attached`; a network error → backoff; the 5th failure → `failed`.
  - Verify: vitest passes.
- **S4.7 Triggers.**
  - Test: the driver runs on app open, `online`, visible, and an Android `sync` event message from the SW.
  - Verify: vitest passes.
- **S4.8 Camera capture flow.**
  - File: `src/go/screens/Photos.tsx` (multi-shot via `<input capture>`, label chip default Before; After when ready).
  - Test: the label default rules.
  - Verify: vitest passes.
- **S4.9 Thumbnails.**
  - Test: pending → progress ring; failed → red badge + Retry; "3 photos uploading, keep Tekly Go open" while pending.
  - Verify: vitest passes.
- **S4.10 More → Uploads.**
  - Test: lists failed uploads; Retry resets them.
  - Verify: vitest passes.
- **S4.11 E2E.**
  - File: `e2e/go/photo.spec.ts`: `context.setOffline(true)`, take a photo, go online, and the thumbnail turns solid.
  - Verify: playwright passes.
- **S4.12 Device check ✋.** On the iPhone: take 5 photos, close the app before the upload finishes, reopen, and all 5 attach.

### S5: Job timer
- **S5.1 Timer on Job** (uses the existing `/api/timeclock/start|stop|active` through `lib/useJobTimer.ts`).
  - Test: start → `hh:mm` running in tabular figures; another job asks "Switch from WO-00411?"; hidden for desk and owner.
  - Verify: vitest passes.
- **S5.2 Shift clock-in in More.**
  - Test: the "Clock in" row calls `/api/timeclock/shift/clock-in` and shows "On shift since 08:02".
  - Verify: vitest passes.

### S6: Find
- **S6.1 `phone_digits`.**
  - Files: a migration + backfill + a save hook.
  - Test: `test_client_phone_digits.py` ("+44 (7700) 900-123" → "447700900123").
  - Verify: alembic upgrade, then pytest.
- **S6.2 `GET /api/go/search` (B4).**
  - Test: `test_go_search.py`: last-4 matches a formatted phone; WO ranks first; IMEI matches; limit 20; rows include `balance_due`.
  - Verify: pytest passes.
- **S6.3 Find screen.**
  - File: `src/go/screens/Find.tsx`.
  - Test: autofocus; a 200 ms debounce leads to one call; results use `JobRow` + a balance line.
  - Verify: vitest passes.
- **S6.4 Recents.**
  - File: `src/go/features/find/recents.ts`.
  - Test: dedupe, cap 5, newest first.
  - Verify: vitest passes.
- **S6.5 Wedge scanner.**
  - Test: "WO-00412\n" typed in the field → `routeScan` → the job.
  - Verify: vitest passes.
- **S6.6 E2E.**
  - File: `e2e/go/find.spec.ts`.
  - Verify: playwright passes.

### S7: Check in + label
- **S7.1 Passcode write (B3).**
  - Test: `test_go_passcode.py::test_write_encrypts`.
  - Verify: pytest passes.
- **S7.2 Passcode reveal.**
  - Test: `test_reveal_audit_row`, and `test_reveal_needs_permission` (new `can_view_device_passcode`).
  - Verify: pytest passes.
- **S7.3 Intake, existing client (B5).**
  - Test: `test_go_intake.py::test_existing_client`.
  - Verify: pytest passes.
- **S7.4 Intake, new client, dedupe by E.164.**
  - Test: `test_new_client_dedupes_phone`.
  - Verify: pytest passes.
- **S7.5 Intake idempotency.**
  - Test: `test_same_key_one_order`.
  - Verify: pytest passes.
- **S7.6 Consent + signature.**
  - Test: `test_consent_log_written`.
  - Verify: pytest passes.
- **S7.7 Single-job label PDF (B6).**
  - Do: reuse the pilot kit's label renderer.
  - Test: `test_go_label_pdf.py`: `application/pdf`, page size = template, the text contains the WO number.
  - Verify: pytest passes.
- **S7.8 Draft.**
  - File: `src/go/features/checkin/draft.ts` (IndexedDB; uses `lib/newJob.ts`).
  - Test: it survives a reload; `missingFields` blocks Next.
  - Verify: vitest passes.
- **S7.9 Customer step.**
  - File: `src/go/screens/checkin/Customer.tsx`.
  - Test: typing digits shows matches; paste → `splitNamePhone`.
  - Verify: vitest passes.
- **S7.10 Device step.**
  - File: `Device.tsx` (kind chips, typeahead, **Scan IMEI** with a Luhn check, colour, passcode).
  - Test: a bad Luhn shows "That doesn't look like an IMEI".
  - Verify: vitest passes.
- **S7.11 Problem step.**
  - File: `Problem.tsx`.
  - Test: the due presets are in shop time.
  - Verify: vitest passes.
- **S7.12 Sign + create.**
  - File: `Sign.tsx`.
  - Test: one `POST /api/go/intake` with an `Idempotency-Key`; success shows Print label / Text tracking link.
  - Verify: vitest passes.
- **S7.13 Print.**
  - File: `src/go/features/checkin/printLabel.ts`: iPhone → fetch the PDF → `navigator.share({files:[pdf]})`; Android/desktop → open the PDF in a new tab for print.
  - Test: the UA-based branch is chosen correctly.
  - Verify: vitest passes.
- **S7.14 Offline check-in.**
  - Test: offline Create → "Will check in when back online"; the label is offered after sync.
  - Verify: vitest passes.
- **S7.15 E2E.**
  - File: `e2e/go/check-in.spec.ts`; time the flow (< 60 s).
  - Verify: playwright passes.
- **S7.16 Device check ✋.** On the iPhone, print a label through the share sheet to the shop's printer. Also test `window.print()` once and record the result in the spec §6.

### S8: Push + owner glance
- **S8.1 `job.overdue` sweep.**
  - File: `backend/services/overdue_push.py`.
  - Test: `test_overdue_push_once_per_job_shop_tz`.
  - Verify: pytest passes.
- **S8.2 `approval.needed` / `refund.requested` / `part.arrived` / `daily.summary`.**
  - File: `test_push_kinds.py` (+ the `approval_push_threshold` migration).
  - Verify: pytest passes.
- **S8.3 Payload rule.**
  - Test: `test_push_payload_has_no_phone_or_body` and `test_push_has_url`.
  - Verify: pytest passes.
- **S8.4 `GET /api/go/badge`.**
  - Test: it counts the role's needs-you items.
  - Verify: pytest passes.
- **S8.5 `GET /api/go/glance` (B9).**
  - Test: `test_go_glance.py` (promised-not-ready, shop-tz revenue, `max-age=60`).
  - Verify: pytest passes.
- **S8.6 Fix the company-dashboard UTC day.**
  - File: `backend/routers/reports.py`.
  - Test: `test_company_dashboard_today_shop_tz.py`.
  - Verify: pytest passes.
- **S8.7 Push priming.**
  - File: `src/go/features/push/Prime.tsx`. It shows only when standalone (iPhone) or installed, then the prompt follows the tap.
  - Test: not standalone on iPhone → it shows "Add to Home Screen first" with the install steps instead.
  - Verify: vitest passes.
- **S8.8 SW push handler.**
  - File: `public/go/sw.js`: show the notification, set the badge from `data.badge`, and a click opens `data.url`.
  - Test: `sw.test.ts`.
  - Verify: vitest passes.
- **S8.9 Badge on open.**
  - Test: on app open, `setAppBadge(count)` from `/api/go/badge` is called when supported, and `clearAppBadge` at 0.
  - Verify: vitest passes.
- **S8.10 Notification settings** with role defaults.
  - Test: the owner defaults are on for approvals, refunds and overdue.
  - Verify: vitest passes.
- **S8.11 Glance card.**
  - File: `src/go/features/glance/GlanceCard.tsx`.
  - Test: 4 tappable figures; red only when promised-not-ready > 0 and past due.
  - Verify: vitest passes.
- **S8.12 Device check ✋.** On the iPhone (installed), get an overdue push, tap it and land on the job. The badge count matches.

### S9: More, devices, lock (always last)
- **S9.1 Devices.**
  - Test: "This phone" is marked; "Sign out other phones" calls B0.2's `sign-out-others`.
  - Verify: vitest passes.
- **S9.2 App lock** (if the M0.4.7 passkey test passed).
  - Test: after 10 min idle the lock screen shows and `passkeyCheck` unlocks it; on failure → sign-in.
  - Verify: vitest passes.
- **S9.3 Passcode reveal.**
  - Test: it asks for the lock first, then reveals; it auto-hides after 30 s.
  - Verify: vitest passes.
- **S9.4 Appearance.**
  - Test: Dark persists; the default stays Light.
  - Verify: vitest passes.
- **S9.5 "Install on another phone"** links to the Settings → Tekly Go QR.
  - Verify: vitest passes.
- **S9.6 Sign-out wipe.**
  - Test: it clears the query cache, queue key, photo store and SW caches (`CLEAR_DATA`).
  - Verify: vitest passes.

### M1 orderings: the pilot picks one

F is first in both, and S9 is last.

| Step | **Tech-first** (techs used phones at the bench) | **Counter/owner-first** (phones stayed at the desk / with the owner) |
|---|---|---|
| 1 | F | F |
| 2 | **S2** Scan → Job | **S6** Find |
| 3 | **S3** Status + notify | **S2** Job screen (scan comes along, not promoted) |
| 4 | **S4** Photos | **S7** Check in + label |
| 5 | **S1** Today (tech sections) | **S3** Status + notify |
| 6 | **S5** Job timer | **S8** Push + owner glance |
| 7 | **S6** Find | **S1** Today (desk / owner sections) |
| 8 | **S7** Check in | **S4** Photos |
| 9 | **S8** Push + glance | **S5** Job timer |
| 10 | S9 | S9 |
| **Hand to the pilot shop after** | step 4 | step 4 |
| **Opens on** | Scan for techs, Today for others | Today for everyone |

If the pilot is unclear, use **tech-first**: scanning is what competitors don't do well.

---

## M2: Phone extras (≈ 1 week)

- **M2.1 Android long-press shortcuts.**
  - Do: the manifest has Scan, Find, Check in; each opens its screen directly.
  - Test: `manifest.test.ts`.
  - Device check ✋ on Android.
- **M2.2 Android share target.**
  - Files: manifest `share_target` (POST multipart to `/go/share`), and the SW handles it and stores the images in the photo store, then opens a job picker.
  - Test: `sw.test.ts`: a share POST becomes pending photo rows.
  - Device check ✋ from Android Photos.
- **M2.3 Android NFC.**
  - Do: More → "Write tag for this job" (`NDEFReader.write` with the job link); reading on Scan when it's supported.
  - Test: an NDEF URL record → `routeScan`.
  - Device check ✋.
- **M2.4 iPhone "Hey Siri" how-to.**
  - Do it **only if** a device test shows a Shortcuts "Open URL" to `/go/scan` opens the installed app, not Safari.
  - Then: More → "Add Siri shortcut" shows 3 steps and a pre-filled link.
  - Device check ✋ first; record the result in spec §6.
- **M2.5 Daily summary push** for owners (opt-in, at the shop's closing time).
  - Test: `test_daily_summary_at_close_shop_tz`.
- **M2.6 Metrics.**
  - Do: `POST /api/go/events` (batched), extending the pilot kit's tracking.
  - Test: batching every 30 s, dropped after 1,000 offline.

## Later: the native store app

When the owner is ready, the native plan in [`tekly-go-native-plan.md`](tekly-go-native-plan.md) applies, with re-verified facts. The browser work carries over:
- `lib/` modules, backend aggregates (`/api/go/*` → `/api/mobile/v1`), push kinds, tokens and screen designs all transfer;
- the browser app stays as the no-install fallback.

## Risks

| Risk | Mitigation |
|---|---|
| The pilot shows techs won't use phones at the bench | Counter/owner-first ordering |
| iPhone camera re-prompts | One long-lived stream on one route (S2.7); a device check |
| iPhone only uploads while the app is open | The file-first store, "keep Tekly Go open" banner, and retries on every open (S4) |
| Push on iPhone needs install first | The install page (M0.4) leads into push priming (S8.7) |
| Passkeys may not work in iPhone standalone mode | Device test in M0; fallback to sign-in |
| Printing from an iPhone home-screen app | PDF through the share sheet (S7.13) |
| Old app versions on phones | The SW update banner + 426 minimum build (M0.3.5, F2) |
