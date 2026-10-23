# Tekly Go (native): implementation plan

Status: **draft for owner approval. Nothing here is started.** Date: 2026-10-23.
Spec: [`tekly-go-native-spec.md`](tekly-go-native-spec.md). Section references (§n) point there.

## How to use this plan

- **Gate:** no app code until the one-shop QR pilot reports. The pilot picks the M1 ordering (below). M0 can start the day the pilot ends.
- **Every task is 2–5 minutes** and follows TDD:
  1. write the test named in *Test first*;
  2. run it and see it **fail**;
  3. make the change in *Do*;
  4. run *Verify* and see it **pass**;
  5. commit, with the task ID in the message (e.g. `GO-M1-S3.2: …`).
- **Commands** (run from the repo root unless noted):
  - Backend test: `pytest -q backend/tests/<file>::<test>`
  - Mobile unit test: `cd mobile && npx jest <path> -t "<name>"`
  - Mobile typecheck: `cd mobile && npx tsc --noEmit`
  - E2E flow: `cd mobile && maestro test .maestro/<flow>.yaml` (against a `development` build on the simulator)
- **Backend conventions:**
  - routers are `backend/routers/<name>.py`, added to `ROUTERS` in `main.py`;
  - migrations go in `backend/migrations/versions/`;
  - tests use the `client` / `tenant` / `db` fixtures from `backend/tests/conftest.py`;
  - the mobile router package is `backend/routers/mobile_v1/` (one module per area, one `router` exported from `__init__.py`).
- **Base branch:** all work branches from the integration branch (`launch/audit-fixes` today), **after B0** (below) has landed there.

---

## B0: Prerequisites (before M0 ends)

These are not 2-minute tasks. They are merges of work that already exists, listed so nothing starts on a stale base.

| ID | Task | Done when |
|---|---|---|
| B0.1 | Merge the Go PWA backend from PR #10 (`go/pay-reauth` / `450a9d2`): idempotency service, `intake-photos/add`, `/api/mobile/*` messaging, `OrderIn.due_at`, shop-tz dashboard | `pytest -q` is green on the integration branch, and `git grep -n "idempotency_records" -- backend` finds the model |
| B0.2 | The device-sessions work (`auth_sessions`, `ds144a1`, `/api/auth/sessions*`) is committed by its session and merged | `git grep -n "auth_sessions" -- backend/migrations` finds the migration; its tests pass |
| B0.3 | Owner starts the Apple Developer **Organization** enrolment (D-U-N-S) and the Google Play organisation account | Both accounts are active; the team ID is noted in `mobile/README.md` |

---

## M0: Scaffold, tokens, CI (≈ 3 days)

### M0.1 Project
- **M0.1.1 Create the Expo app.**
  - Files: `mobile/` (new).
  - Do: `npx create-expo-app@latest mobile --template blank-typescript`; set `"expo": "~58"`; delete the sample assets.
  - Verify: `cd mobile && npx expo-doctor` reports no issues.
- **M0.1.2 Add Expo Router.**
  - Files: `mobile/package.json`, `mobile/app/_layout.tsx`, `mobile/app/index.tsx`.
  - Test first: `mobile/app/__tests__/root.test.tsx` renders the root layout and finds the text "Tekly Go".
  - Verify: the jest test passes, and `npx expo start --ios` shows the screen.
- **M0.1.3 Jest + RNTL.**
  - Files: `mobile/jest.config.js` (preset `jest-expo`), `mobile/jest.setup.ts`.
  - Test first: `mobile/src/__tests__/smoke.test.ts` `expect(1+1).toBe(2)`.
  - Verify: `npx jest` shows 1 test passing.
- **M0.1.4 TypeScript strict + path alias `@/` → `src/`.**
  - Files: `mobile/tsconfig.json`, `mobile/babel.config.js`.
  - Verify: `npx tsc --noEmit` exits 0, and an import from `@/` resolves in the jest smoke test.
- **M0.1.5 ESLint (expo config) + Prettier settings matching `frontend-react`.**
  - Files: `mobile/eslint.config.js`.
  - Verify: `npx eslint .` exits 0.
- **M0.1.6 App identity.**
  - Files: `mobile/app.config.ts`. Set:
    - name "Tekly Go", slug `tekly-go`, scheme `teklygo`;
    - `ios.bundleIdentifier` / `android.package` = `com.tekly.go` (pending Q7);
    - `runtimeVersion: { policy: "fingerprint" }`;
    - `userInterfaceStyle: "light"`.
  - Test first: `mobile/src/__tests__/appConfig.test.ts` asserts the bundle ID, the scheme, and the fingerprint policy.
  - Verify: the test passes, and `npx expo config --type public` shows the values.
- **M0.1.7 Icons and splash.**
  - Files: `mobile/assets/icon.png` (the sapphire hex-nut on white), `adaptive-icon.png`, `splash-icon.png`; `expo-splash-screen` config with a white background.
  - Verify: `npx expo prebuild --clean --no-install` succeeds; inspect the `ios/` icon set, then delete `ios/` and `android/` (they are git-ignored).
- **M0.1.8 Ignore native folders and the EAS cache.**
  - Files: `mobile/.gitignore` (`ios/`, `android/`, `.expo/`, `*.jks`, `*.p8`, `*.p12`, `credentials.json`).
  - Verify: `git status` after a prebuild shows no native files.

### M0.2 Tokens and theme
- **M0.2.1 Token generator (test first).**
  - Files: `mobile/scripts/gen-tokens.mjs`, `mobile/scripts/__tests__/gen-tokens.test.mjs`.
  - Test first: a fixture `NEUTRALS = {canvas: ["#f7f8fd", "#0e1119"]}` produces `{light:{canvas:"#f7f8fd"}, dark:{canvas:"#0e1119"}}`.
  - Do: import `../../frontend-react/theme.tokens.js` (`NEUTRALS` + the tint families via `tailwindcss/colors`, added as a mobile devDependency) and write `src/theme/tokens.generated.ts`.
  - Verify: `npx jest scripts` passes.
- **M0.2.2 Generate and commit the tokens.**
  - Files: `mobile/src/theme/tokens.generated.ts`.
  - Verify: `node scripts/gen-tokens.mjs` writes the file; `grep primary-fill` finds `#0059ba`.
- **M0.2.3 `--check` mode.**
  - Files: `mobile/scripts/gen-tokens.mjs`.
  - Test first: with a stale file, `--check` exits 1 and prints the diff.
  - Verify: the test passes; `node scripts/gen-tokens.mjs --check` exits 0 on a clean tree.
- **M0.2.4 Scale.**
  - Files: `mobile/src/theme/scale.ts`, holding the five type steps (§6), spacing 4/8/12/16/20/24/32, radii 8/12/16, hairline 0.5 and the spring config (no overshoot).
  - Test first: `src/theme/__tests__/scale.test.ts` checks the exact 28/34 … 12/16 pairs and that no step is below 12.
  - Verify: the test passes.
- **M0.2.5 Theme hook, light by default.**
  - Files: `mobile/src/theme/useTheme.ts`, `src/theme/ThemeProvider.tsx`.
  - Test first: with no saved preference and the system set to dark, `useTheme().mode === "light"`; with "system" saved and the system dark, the mode is "dark".
  - Verify: the test passes.
- **M0.2.6 Fonts.**
  - Files: `mobile/assets/fonts/Geist-*.ttf`, `GeistMono-Regular.ttf`, and the `expo-font` plugin in `app.config.ts`.
  - Verify: `npx expo prebuild --no-install` embeds the fonts (check `Info.plist` `UIAppFonts`), then clean up.

### M0.3 UI primitives (each: test first with RNTL, then the component)
- **M0.3.1 `Text`.**
  - File: `src/ui/Text.tsx` with `variant` = title|heading|body|subhead|caption, a `mono` prop and a `tone` prop.
  - Test: `variant="caption"` renders fontSize 12; `mono` uses GeistMono; numbers get `fontVariant: ['tabular-nums']`.
- **M0.3.2 `GroupedList` + `Row`.**
  - File: `src/ui/GroupedList.tsx`.
  - Test: rows render 0.5 px separators except after the last; `onPress` fires; the pressed state changes the background, not the scale.
- **M0.3.3 `Button`.**
  - File: `src/ui/Button.tsx`: primary (sapphire fill) / plain; minimum height 44.
  - Test: the primary background equals `tokens.light["primary-fill"]`; disabled blocks `onPress`.
- **M0.3.4 `StatusDot` + `StatusPill`.**
  - File: `src/ui/Status.tsx`.
  - Test: given `{name:"Ready", color:"#22c55e"}`, the dot renders that colour and the pill uses a tinted background with ink text; the name is always shown.
- **M0.3.5 `Avatar`.**
  - File: `src/ui/Avatar.tsx`: initials with a deterministic tint (same algorithm as `frontend-react` `look.tsx`).
  - Test: "Ana Lee" → "AL" with the same tint index as the web helper (copy 3 cases from the web test).
- **M0.3.6 `Sheet`.**
  - File: `src/ui/Sheet.tsx`, wrapping the native form-sheet presentation with detents.
  - Test: it renders its children when open and calls `onClose` on dismiss.
- **M0.3.7 `Field`.**
  - File: `src/ui/Field.tsx`: a floating label like the web `authUi.tsx`, an error line, and a 44 pt minimum height.
  - Test: the error text renders under the field with `accessibilityLiveRegion="polite"`.
- **M0.3.8 `Banner`.**
  - File: `src/ui/Banner.tsx`: offline, update and held-sync banners.
  - Test: `kind="offline"` renders "Offline · updated {time}".
- **M0.3.9 `haptics` wrapper.**
  - File: `src/ui/haptics.ts`: select / impact / success / warning, a no-op on web.
  - Test: `success()` calls `Haptics.notificationAsync(Success)` (module mocked).

### M0.4 Ported pure libs (copy, run the existing test unchanged)
- **M0.4.1 `scanRoute`.**
  - Do: copy `frontend-react/src/go/lib/scanRoute.ts` and its test from `450a9d2` to `mobile/src/lib/`.
  - Verify: `npx jest src/lib/scanRoute` passes unchanged.
- **M0.4.2 `/go/j/{code}` pattern.**
  - Test first: add a case to `scanRoute.test.ts`: `https://app.tekly.io/go/j/ABC123` → `{kind:"job", …}` with no resolve call.
  - Do: extend `directTarget`.
  - Verify: the test passes.
- **M0.4.3 `errors.ts` + `statusChange`.**
  - Do: create `mobile/src/lib/errors.ts` (`readableError`, copied from `frontend-react/src/api/client.ts`); copy `statusChange.ts` and its test and change one import.
  - Verify: `npx jest src/lib/statusChange` passes.
- **M0.4.4 Copy `queue.ts`, `time.ts`, `messaging.ts`, `newJob.ts`, `idle.ts` and their tests.**
  - Do: replace `isIOS(ua)` with `Platform.OS === "ios"` behind the same signature.
  - Verify: `npx jest src/lib` passes everything.
- **M0.4.5 Offline queue: cipher seam.**
  - Test first: in `offlineQueue.test.ts`, add "uses the injected cipher": a fake cipher records its `enc` / `dec` calls, and the existing replay cases still pass.
  - Do: replace `subtle` in `QueueDeps` with `cipher: { enc(obj), dec(payload) }`; add `aesCipher(subtle)` (the current behaviour) and `identityCipher`.
  - Verify: `npx jest src/lib/offlineQueue` passes.
- **M0.4.6 Offline queue: Idempotency-Key.**
  - Test first: an enqueued item gets `headers["Idempotency-Key"]`, a UUID, and the replay sends the same key twice across retries.
  - Do: implement it.
  - Verify: the test passes.
- **M0.4.7 SQLite queue store.**
  - File: `mobile/src/storage/queueStore.ts`, implementing `QueueStore` over `expo-sqlite`.
  - Test first: `src/storage/__tests__/queueStore.test.ts`, using the in-memory DB mock: add → all → remove round-trip, meta get/put.
  - Verify: the test passes.
- **M0.4.8 Encrypted DB.**
  - Files: `mobile/src/storage/db.ts`, which opens the DB with the SQLCipher key from `expo-secure-store` (created on first run); `app.config.ts` gets the `expo-sqlite` plugin `{useSQLCipher:true}`.
  - Test first: `db.test.ts`: the first open creates a key and the second open reuses it (secure-store mocked).
  - Verify: the test passes.

### M0.5 CI and EAS
- **M0.5.1 CI job.**
  - File: `.github/workflows/ci.yml`. Add a `mobile` job:
    - path filter `mobile/**`, `frontend-react/theme.tokens.js`, `backend/routers/mobile_v1/**`;
    - Node 22, `npm ci`;
    - `npx tsc --noEmit`, `npx eslint .`, `npx jest --coverage`, `node scripts/gen-tokens.mjs --check`, `npx expo-doctor`.
  - Verify: push the branch; the job is green in the Actions log.
- **M0.5.2 Coverage floor.**
  - File: `mobile/jest.config.js`, with `coverageThreshold` `{"./src/lib/": {lines: 70}}`.
  - Verify: `npx jest --coverage` passes; deleting a test makes it fail (then restore the test).
- **M0.5.3 EAS project.**
  - Files: `mobile/eas.json` with `development` (developmentClient, internal), `preview` (internal, channel `preview`) and `production` (channel `production`, `autoIncrement`).
  - Do: `eas init` and `eas build:configure`.
  - Verify: `eas build --profile development --platform ios --local` produces an app (or a cloud build succeeds).
- **M0.5.4 EAS Update.**
  - Do: `eas update:configure`.
  - Verify: `eas update --channel preview --message "smoke"`, and the preview build on a device shows the update after a relaunch.
- **M0.5.5 Sentry.**
  - Files: `@sentry/react-native` + its config plugin; `src/observability.ts` (DSN from env, release = build).
  - Test first: `observability.test.ts`: `init` is not called when the DSN is empty.
  - Verify: a forced test crash in a dev build appears in Sentry with symbolicated frames.
- **M0.5.6 Maestro smoke.**
  - File: `mobile/.maestro/launch.yaml`: launch the app, assert "Tekly Go".
  - Verify: `maestro test .maestro/launch.yaml` passes on the simulator.
- **M0.5.7 Workflow on merge.**
  - File: `mobile/.eas/workflows/preview.yml`: build preview for iOS + Android, then run the Maestro flows.
  - Verify: a manual `eas workflow:run preview.yml` is green.

**M0 done:**
- CI is green, with tokens in sync and ported libs passing.
- A dev build runs on a real iPhone and Android phone, showing the themed placeholder.
- An OTA update reaches a preview build.

---

## M1: The core loop (≈ 3 weeks)

M1 is built as **one foundation (F) plus eight self-contained slices (S1–S8)**.
- Each slice carries its own backend tasks, app tasks and E2E flow, and depends only on F.
- So the pilot result just sets the **order of slices**.
- Two orderings are given at the end. If the pilot is unclear, use **tech-first**: the scan loop is the differentiator.

### F: Foundation (always first, ≈ 3 days)

**F1 Mobile API base (backend)**
- **F1.1 Package skeleton.**
  - Files: `backend/routers/mobile_v1/__init__.py` (an `APIRouter(prefix="/api/mobile/v1")`); `main.py` adds it to `ROUTERS`.
  - Test first: `backend/tests/test_mobile_v1_config.py::test_prefix_mounted`: `GET /api/mobile/v1/config` is not 404.
  - Verify: `pytest -q backend/tests/test_mobile_v1_config.py`.
- **F1.2 `GET /config` (B1).**
  - Files: `backend/routers/mobile_v1/config.py`; `backend/config.py` gets `MOBILE_MIN_BUILD_IOS/ANDROID`, `MOBILE_LATEST_BUILD_*`, `MOBILE_STORE_URL_*`, `MOBILE_FEATURES` (JSON).
  - Test first: `test_config_shape` asserts the keys `min_build.ios`, `latest_build.android`, `store_url`, `features`, with no auth needed.
  - Verify: pytest passes.
- **F1.3 Client header parsing.**
  - File: `backend/routers/mobile_v1/deps.py` `client_build(request)`, which parses `X-Tekly-Client: go-ios/1.4.0+87` → `("ios", 87)`.
  - Test first: `test_mobile_v1_deps.py::test_parse_client_header` covers good, missing and garbage values.
  - Verify: pytest passes.
- **F1.4 426 gate.**
  - File: `deps.py` `require_supported_client`, a router-level dependency.
  - Test first: `test_old_build_gets_426`: with min 50 and the header build 49, the response is 426 `{code:"client_too_old", store_url}`; a missing header is allowed (for dev tools).
  - Verify: pytest passes.
- **F1.5 Error shape.**
  - File: `backend/routers/mobile_v1/errors.py`, an exception handler scoped to the mobile prefix that maps `HTTPException(detail={code, message})` to `{code, message}`.
  - Test first: `test_error_shape`.
  - Verify: pytest passes.
- **F1.6 Contract snapshot harness.**
  - File: `backend/tests/test_mobile_v1_contract.py`, with a helper `assert_keys(resp_json, "board_row")` that compares against `backend/tests/snapshots/mobile_v1/*.json`.
  - Test first: the config snapshot.
  - Verify: pytest passes.

**F2 Mobile auth (backend, B2)**
- **F2.1 Migration.**
  - File: `backend/migrations/versions/<rev>_mobile_refresh_tokens.py`, creating `mobile_refresh_tokens {id, session_id FK auth_sessions ON DELETE CASCADE, token_hash, family_id, issued_at, used_at, expires_at}`.
  - Test first: `test_mobile_v1_auth.py::test_table_exists`.
  - Verify: `alembic -c backend/alembic.ini upgrade head`, then pytest.
- **F2.2 `POST /auth/token`.**
  - File: `backend/routers/mobile_v1/auth.py`, reusing the password and MFA checks from `routers/auth.py`.
  - Test first: `test_token_returns_pair`: the response has `access_token` (exp ≈ 15 min), `refresh_token`, `session_id`, and creates an `auth_sessions` row labelled from `X-Tekly-Client`.
  - Verify: pytest passes.
- **F2.3 MFA passthrough.**
  - Test first: `test_token_mfa_required`: a user with MFA and no code gets 401 with the `X-MFA-Required: 1` header; with a code in `client_secret` the request succeeds.
  - Verify: pytest passes.
- **F2.4 Refresh rotation.**
  - Test first: `test_refresh_rotates`: refresh returns a new pair, and the old refresh token is marked used.
  - Verify: pytest passes.
- **F2.5 Reuse detection.**
  - Test first: `test_refresh_reuse_revokes_session`: presenting a used token returns 401 and revokes the session, and the newest token stops working too.
  - Verify: pytest passes.
- **F2.6 Logout.**
  - Test first: `test_logout_revokes_session_and_push`: the session is revoked (push-device cascade asserted later, in S8).
  - Verify: pytest passes.
- **F2.7 Session revoke kills refresh.**
  - Test first: `test_admin_signout_blocks_refresh`, using B0.2's admin sign-out route.
  - Verify: pytest passes.

**F3 App shell (mobile)**
- **F3.1 Secure token store.**
  - File: `mobile/src/storage/secure.ts` (`getRefresh` / `setRefresh` / `clear`).
  - Test first: `secure.test.ts` round-trip with the mock.
  - Verify: jest passes.
- **F3.2 API client.**
  - File: `mobile/src/api/client.ts`: base URL from config; sends `X-Tekly-Client`; the access token is kept in memory.
  - Test first: `client.test.ts::adds client header`, using a fetch mock.
  - Verify: jest passes.
- **F3.3 Auto-refresh.**
  - Test first: `client.test.ts::refreshes once on 401 then retries`, and `::two concurrent 401s share one refresh`.
  - Verify: jest passes.
- **F3.4 426 handling.**
  - Test first: `client.test.ts::426 emits forceUpdate`.
  - Do: an event emitter; `app/_layout.tsx` shows the `ForceUpdate` screen.
  - Verify: jest passes.
- **F3.5 Generated API types.**
  - File: `mobile/scripts/gen-api-types.mjs`, which fetches the mobile-tagged OpenAPI from a local backend (or reads `backend/openapi.mobile.json` exported by a pytest helper) and runs `openapi-typescript` to write `src/api/schema.d.ts`; it also has a `--check` mode.
  - Test first: the check fails on a stale file.
  - Verify: `node scripts/gen-api-types.mjs --check` exits 0; the CI job gains this step.
- **F3.6 Sign-in screen.**
  - File: `mobile/app/sign-in.tsx`: email, then password, then a code step on `X-MFA-Required` (one field per step, like the web).
  - Test first: `app/__tests__/sign-in.test.tsx`: a 401 + MFA header shows the "Enter the 6-digit code" step.
  - Verify: jest passes.
- **F3.7 Auth gate.**
  - File: `mobile/app/_layout.tsx`, which redirects to `/sign-in` when there is no refresh token.
  - Test first: `root.test.tsx::redirects when signed out`.
  - Verify: jest passes.
- **F3.8 Native tabs.**
  - File: `mobile/app/(tabs)/_layout.tsx`, using `NativeTabs` with Today, Find, Scan and More (SF Symbols / Material icons).
  - Test first: `tabs.test.tsx` renders the 4 tab labels.
  - Verify: jest passes; on the simulator the tabs switch with no remount flash.
- **F3.9 Query client + persistence.**
  - File: `mobile/src/api/queryClient.ts`: TanStack Query with an SQLite persister and `staleTime` of 30 s.
  - Test first: `queryClient.test.ts`: a persisted query rehydrates.
  - Verify: jest passes.
- **F3.10 Online/offline wiring.**
  - File: `mobile/src/api/online.ts`, connecting `@react-native-community/netinfo` to the Query `onlineManager` and to the queue's `online()`.
  - Test first: going offline pauses mutations; going online flushes the queue (mocks).
  - Verify: jest passes.
- **F3.11 Role derivation.**
  - File: `mobile/src/lib/role.ts` `roleFromMe(me)`.
  - Test first: `role.test.ts`: `is_owner` → owner; `can_create_orders` without reports → desk; otherwise tech; the override wins.
  - Verify: jest passes.
- **F3.12 E2E sign-in.**
  - File: `mobile/.maestro/sign-in.yaml`, signing into the seeded demo shop.
  - Verify: `maestro test` passes.

---

### S1: Today board
**Backend (B3)**
- **S1.1 Board row serializer.**
  - File: `backend/routers/mobile_v1/board.py` `_row(order, now_local)`.
  - Test first: `test_mobile_v1_board.py::test_row_fields` asserts every key in spec B3 (`status.color`, `client.phone_last4`, `device.label`, `age_days`, `balance_due`, …).
  - Verify: pytest passes.
- **S1.2 Shop-tz sections.**
  - Test first: `test_sections_in_shop_tz`: with the shop in `America/Los_Angeles`, a job due 23:30 local on a day that is already "tomorrow" in UTC lands in `due_today`, and a job past due lands in `late`.
  - Do: use `services/dates.local_now` / `shop_timezone`.
  - Verify: pytest passes.
- **S1.3 Scope.**
  - Test first: `test_scope_mine_vs_shop`: `mine` returns only the caller's assigned jobs; `shop` requires `can_view_all_orders` (403 otherwise).
  - Verify: pytest passes.
- **S1.4 ETag.**
  - Test first: `test_etag_304`: a repeat request with `If-None-Match` returns 304.
  - Verify: pytest passes.
- **S1.5 Fix the board overdue tz bug.**
  - File: `backend/routers/orders.py` (the `overdue` computation, currently `utcnow` vs a shop-local `due_at`).
  - Test first: `backend/tests/test_orders_board_overdue_tz.py`.
  - Verify: pytest passes.
- **S1.6 Contract snapshot** `board_row.json`.
  - Verify: `pytest -q backend/tests/test_mobile_v1_contract.py`.

**App**
- **S1.7 `useBoard(scope)` hook.**
  - File: `mobile/src/features/today/useBoard.ts`.
  - Test first: it maps the API sections to the role's ordering (§4.2 table) and hides empty sections.
  - Verify: jest passes.
- **S1.8 `JobRow`.**
  - File: `mobile/src/features/today/JobRow.tsx`.
  - Test first: it renders the name, "iPhone 14 Pro · Cracked screen", a mono `WO-00412` and "2 days late" in the danger colour only when `is_late`.
  - Verify: jest passes.
- **S1.9 Today screen.**
  - File: `mobile/app/(tabs)/today.tsx`: a grouped list, pull to refresh, the empty state copy from §4.2.
  - Test first: `today.test.tsx`: with all sections empty, it shows the "Nothing due today…" copy and no zero tiles.
  - Verify: jest passes.
- **S1.10 Offline caption.**
  - Test first: when offline with cached data, it shows "Offline · updated 14:02".
  - Verify: jest passes.
- **S1.11 Row move animation.**
  - Do: `LayoutAnimation` / Reanimated layout transition with no overshoot; respects reduced motion.
  - Test first: with reduce-motion on, no layout animation is configured (mock `AccessibilityInfo`).
  - Verify: jest passes; check visually on the simulator.
- **S1.12 E2E.**
  - File: `.maestro/today.yaml`: sign in, see the seeded late job at the top in red.
  - Verify: maestro passes.

### S2: Scan → job, and the Job screen (read)
**Backend (B4, B13)**
- **S2.1 Job aggregate.**
  - File: `backend/routers/mobile_v1/jobs.py` `GET /jobs/{id}`.
  - Test first: `test_mobile_v1_jobs.py::test_job_shape` covers the header, `allowed_statuses[{key,name,color,group,needs}]`, `watch_outs`, `photos`, `charges.balance_due` and `history[:5]`.
  - Verify: pytest passes.
- **S2.2 Allowed statuses filtered by record type.**
  - Test first: `test_allowed_excludes_estimate_keys` for a work order.
  - Verify: pytest passes.
- **S2.3 `needs` gates.**
  - Test first: `test_needs_signature_flag`: a status with `require_signature` lists `needs:["signature"]`.
  - Verify: pytest passes.
- **S2.4 `balance_due`.**
  - Test first: `test_balance_due_matches_invoice_preview`: it equals the `invoice-preview` balance for the same order.
  - Verify: pytest passes.
- **S2.5 Record scope.**
  - Test first: `test_job_404_for_other_org` and `test_job_403_outside_record_scope`.
  - Verify: pytest passes.
- **S2.6 `.well-known` routes.**
  - File: `main.py` (above the SPA catch-all) + `backend/routers/wellknown.py`.
  - Test first: `test_wellknown.py`: `GET /.well-known/apple-app-site-association` returns JSON with `applinks.details[0].components` covering `/go/j/*`; `assetlinks.json` has the package and the SHA-256 from config.
  - Verify: pytest passes.
- **S2.7 `/go/j/{code}` web fallback.**
  - Test first: `test_go_link_redirects_to_pwa_job`: a known barcode redirects to `/go/job/{id}`, and an unknown one goes to `/go/scan?code=`.
  - Verify: pytest passes.
- **S2.8 Contract snapshot** `job.json`.

**App**
- **S2.9 Scan screen.**
  - File: `mobile/app/(tabs)/scan.tsx` with `CameraView` (`barcodeTypes: qr, code128, ean13, datamatrix`).
  - Test first: `scan.test.tsx`: the `onBarcodeScanned` mock with "WO-00412" calls `routeScan` once (debounced) and navigates to `/job/<id>`.
  - Verify: jest passes.
- **S2.10 Permission states.**
  - Test first: when denied, it shows "Camera is off for Tekly Go" + Open Settings + "Type a number".
  - Verify: jest passes.
- **S2.11 No match.**
  - Test first: an `{matched:false}` resolve shows "No job for this code" + "Search instead", which goes to Find with the code prefilled.
  - Verify: jest passes.
- **S2.12 Haptic + torch.**
  - Test first: a decode calls `haptics.impact()`; the torch toggle flips `enableTorch`.
  - Verify: jest passes.
- **S2.13 Universal-link landing.**
  - File: `mobile/app/go/j/[code].tsx`, which runs `routeScan` and replaces itself with the job.
  - Test first: `goLink.test.tsx`.
  - Verify: jest passes; `npx uri-scheme open "https://<host>/go/j/ABC" --ios` opens the job.
- **S2.14 Associated domains config.**
  - File: `app.config.ts` (`ios.associatedDomains`, `android.intentFilters` with `autoVerify`).
  - Test first: `appConfig.test.ts` asserts both.
  - Verify: jest passes.
- **S2.15 `useJob(id)`.**
  - File: `src/features/job/useJob.ts`.
  - Test first: it returns cached data instantly, then refetches.
  - Verify: jest passes.
- **S2.16 Job header.**
  - File: `src/features/job/JobHeader.tsx`.
  - Test first: the large title is the customer name (not the WO number); the caption reads "WO-00412 · Booked Tue 14:05"; the status pill is present; "Due Thu 17:00" is red only when late.
  - Verify: jest passes.
- **S2.17 Groups.**
  - File: `src/features/job/JobGroups.tsx` (Details, Photos, Charges, Signature, History).
  - Test first: the balance renders in ink tone, not amber; IMEI is mono.
  - Verify: jest passes.
- **S2.18 Docked action bar.**
  - File: `src/features/job/ActionBar.tsx`.
  - Test first: it shows `suggested_next` ("Mark ready"), and the list's bottom content inset ≥ the bar height.
  - Verify: jest passes.
- **S2.19 Job screen assembly.**
  - File: `mobile/app/job/[id]/index.tsx`.
  - Test first: `job.test.tsx` renders with fixture JSON from the snapshot.
  - Verify: jest passes.
- **S2.20 E2E.**
  - File: `.maestro/scan-to-job.yaml`, using a deep link to stand in for the camera.
  - Verify: maestro passes, and the time to the job is logged.

### S3: Status + notify
**Backend (B16)**
- **S3.1 Idempotent status PATCH.**
  - File: `backend/routers/orders.py` (wire the B0.1 `idempotency` dependency onto `PATCH /api/orders/{id}/status`).
  - Test first: `test_status_idempotent.py`: the same key twice gives one history row and the same response.
  - Verify: pytest passes.
- **S3.2 Mount messaging under v1.**
  - File: `backend/routers/mobile_v1/__init__.py`, re-exporting `mobile_messages` routes at `/api/mobile/v1/orders/{id}/compose`, `/messages`, `/messages/{id}`.
  - Test first: `test_mobile_v1_messages.py::test_compose_reachable_under_v1`.
  - Verify: pytest passes.
- **S3.3 Template hint on status.**
  - Test first: `test_job_status_has_template_flag`: `allowed_statuses[].has_template` is true when a `NotificationTemplate` / `MessageTemplate` matches.
  - Verify: pytest passes.

**App**
- **S3.4 Status sheet.**
  - File: `src/features/job/StatusSheet.tsx`.
  - Test first: it lists the allowed statuses with dots, with `suggested_next` first; selection fires `haptics.select()`.
  - Verify: jest passes.
- **S3.5 Optimistic change.**
  - File: `src/features/job/useChangeStatus.ts`, wrapping the ported `changeStatus` with the queue mutate.
  - Test first: success updates the job and board caches; a 409 restores the old status and shows the `refusal()` text.
  - Verify: jest passes.
- **S3.6 Queued change.**
  - Test first: offline, the change stays on screen with a "Waiting to sync" caption; a held outcome shows "Will sync on shop Wi-Fi".
  - Verify: jest passes.
- **S3.7 "Tell {name}?" chip.**
  - Test first: after a status with `has_template`, the chip appears and hides after 6 s (fake timers); tapping it opens the Message sheet with that template.
  - Verify: jest passes.
- **S3.8 Message sheet.**
  - File: `src/features/job/MessageSheet.tsx`, using compose, the ported `smsLink` / `waLink` and `Linking.openURL`.
  - Test first: tapping SMS calls `openURL("sms:+1…&body=…")` on iOS; WhatsApp is hidden when `consent.whatsapp === false`.
  - Verify: jest passes.
- **S3.9 "Sent?" confirm.**
  - Test first: an `AppState` change back to `active` after opening the composer shows Sent / Not sent, and Sent calls `PATCH /messages/{id}`.
  - Verify: jest passes.
- **S3.10 E2E.**
  - File: `.maestro/status-notify.yaml`: Mark ready → chip → sheet visible.
  - Verify: maestro passes.

### S4: Photos
**Backend (B9)**
- **S4.1 Multipart upload.**
  - File: `backend/routers/mobile_v1/uploads.py` `POST /uploads`, which stores via the existing media service.
  - Test first: `test_mobile_v1_uploads.py::test_multipart_jpeg` returns `{id, url}`.
  - Verify: pytest passes.
- **S4.2 Idempotent upload.**
  - Test first: `test_upload_same_key_same_media`: one blob is stored and the same `id` is returned.
  - Verify: pytest passes.
- **S4.3 Limits.**
  - Test first: `test_upload_rejects_non_image` (415) and `test_upload_8mb_cap` (413).
  - Verify: pytest passes.

**App**
- **S4.4 Uploads table.**
  - File: `mobile/src/storage/uploads.ts`, with a schema and `enqueue` / `next` / `mark` for `pending → uploading → uploaded → attached | failed`.
  - Test first: `uploads.test.ts` covers the state transitions, and `attempts` increments on failure.
  - Verify: jest passes.
- **S4.5 Resize.**
  - File: `src/features/photos/prepare.ts`, using `expo-image-manipulator` with the ported `fitWithin(1600)` at q 0.72.
  - Test first: a 4032×3024 input gives a 1600×1200 output (manipulator mocked).
  - Verify: jest passes.
- **S4.6 Upload driver.**
  - File: `src/features/photos/driver.ts`, using `File#createUploadTask` with an `Idempotency-Key` header, then `intake-photos/add`.
  - Test first: success takes the row to `attached` and deletes the local file; a network error sets `attempts+1` and backoff; the 5th failure sets `failed`.
  - Verify: jest passes.
- **S4.7 Re-drive triggers.**
  - Test first: the driver runs on launch, on foreground and on NetInfo reconnect (mocks).
  - Verify: jest passes.
- **S4.8 Background task.**
  - File: `src/features/photos/backgroundTask.ts`, registering an `expo-background-task` that calls the driver.
  - Test first: the task is registered once and calls `driver.run()`.
  - Verify: jest passes.
- **S4.9 Camera flow.**
  - File: `mobile/app/job/[id]/photos.tsx`, with multi-shot until Done and a label chip (default Before; After when status group = ready).
  - Test first: `photos.test.tsx` covers the label default rules.
  - Verify: jest passes.
- **S4.10 Thumbnails with progress.**
  - Test first: a pending row shows a progress ring; a failed row shows a red badge with Retry.
  - Verify: jest passes.
- **S4.11 More → Uploads.**
  - Test first: it lists failed uploads, and Retry resets `attempts`.
  - Verify: jest passes.
- **S4.12 E2E.**
  - File: `.maestro/photo.yaml`: take a photo with airplane mode on, turn it off, and the thumbnail turns solid.
  - Verify: maestro passes on the simulator, and a real-device run is done by hand once.

### S5: Job timer (tech)
**Backend:** none. `/api/timeclock/start|stop|active` exist and are mounted under v1 in S5.1.
- **S5.1 Mount the timer routes.**
  - Test first: `test_mobile_v1_timer.py::test_start_stop_under_v1`.
  - Verify: pytest passes.
- **S5.2 `useTimer(orderId)`.**
  - File: `src/features/job/useTimer.ts`.
  - Test first: start sets active; a second job's start shows "Switch from WO-00411?"; stop clears.
  - Verify: jest passes.
- **S5.3 Timer action.**
  - Test first: the Job's Timer action shows a running `hh:mm` with tabular figures; it is hidden for desk and owner.
  - Verify: jest passes.

### S6: Find by number
**Backend (B6)**
- **S6.1 `phone_digits` column.**
  - Files: a migration `<rev>_client_phone_digits.py` (column + index + backfill); `models` adds the field; the client save hook keeps it in sync.
  - Test first: `test_client_phone_digits.py`: "+44 (7700) 900-123" → "447700900123".
  - Verify: alembic upgrade, then pytest.
- **S6.2 `GET /search`.**
  - File: `backend/routers/mobile_v1/search.py`.
  - Test first: `test_mobile_v1_search.py`: "0123" finds the client whose formatted phone ends in 0123; "WO-00412" ranks first; an IMEI matches; the limit is 20.
  - Verify: pytest passes.
- **S6.3 Search rows include the balance.**
  - Test first: `test_search_row_has_balance_due`.
  - Verify: pytest passes.
- **S6.4 Contract snapshot** `search_row.json`.

**App**
- **S6.5 Find screen.**
  - File: `mobile/app/(tabs)/find.tsx`: the field is autofocused, debounced 200 ms, and results use `JobRow` + a balance line.
  - Test first: `find.test.tsx`: typing "0123" calls search once after the debounce.
  - Verify: jest passes.
- **S6.6 Recents.**
  - File: `src/features/find/recents.ts` (5 per device, in SQLite).
  - Test first: dedupes, caps at 5, and puts the most recent first.
  - Verify: jest passes.
- **S6.7 Wedge scanner.**
  - Test first: submitting "WO-00412\n" in the field goes through `routeScan` and opens the job.
  - Verify: jest passes.
- **S6.8 E2E.**
  - File: `.maestro/find.yaml`: type the last 4 digits and open the job.
  - Verify: maestro passes, and the p50 time is logged.

### S7: Quick check-in with label
**Backend (B5, B7, B8)**
- **S7.1 Passcode write.**
  - File: `backend/routers/mobile_v1/jobs.py` `PUT /jobs/{id}/device/passcode`, encrypting with the existing field-encryption helper into `Device.passcode_enc`.
  - Test first: `test_mobile_v1_passcode.py::test_write_encrypts` (the stored value differs from the plaintext).
  - Verify: pytest passes.
- **S7.2 Passcode reveal + audit.**
  - Test first: `test_reveal_writes_audit_row`, and `test_reveal_requires_permission` (403 without `can_view_device_passcode`, a new permission defaulted to roles with `can_edit_orders`).
  - Verify: pytest passes.
- **S7.3 Intake, existing client.**
  - File: `backend/routers/mobile_v1/intake.py`.
  - Test first: `test_mobile_v1_intake.py::test_existing_client` creates an order + asset with IMEI, `due_at` and photos, and returns the B4 shape + `label_url` + `tracking_url`.
  - Verify: pytest passes.
- **S7.4 Intake, new client, dedupe by E.164.**
  - Test first: `test_new_client_dedupes_phone`: a second intake with "07700 900123" reuses the "+447700900123" client.
  - Verify: pytest passes.
- **S7.5 Intake idempotency.**
  - Test first: `test_intake_same_key_one_order`.
  - Verify: pytest passes.
- **S7.6 Intake consent + signature.**
  - Test first: `test_intake_writes_consent_log` (SMS and WhatsApp), and the signature is attached.
  - Verify: pytest passes.
- **S7.7 Label PDF.**
  - File: `backend/routers/mobile_v1/labels.py` `GET /jobs/{id}/label.pdf`, rendering the shop's order-sticker template HTML (the existing `documents` render) to PDF at the template size with the QR = `/go/j/{barcode}`.
  - Test first: `test_label_pdf.py`: the response is `application/pdf`, the page size matches the template mm, and the text layer contains the WO number.
  - Verify: pytest passes.
- **S7.8 Contract snapshot** `intake.json`.

**App**
- **S7.9 Check-in draft store.**
  - File: `src/features/checkin/draft.ts` (SQLite-persisted, reusing the ported `newJob.ts` helpers).
  - Test first: the draft survives a reload, and `missingFields` blocks Next.
  - Verify: jest passes.
- **S7.10 Step 1, Customer.**
  - File: `mobile/app/check-in/customer.tsx`: the phone field, typeahead via `/search`, and `splitNamePhone` on paste.
  - Test first: typing digits shows matching customers; picking one fills the draft.
  - Verify: jest passes.
- **S7.11 Step 2, Device.**
  - File: `mobile/app/check-in/device.tsx`: kind chips, brand/model typeahead, Scan IMEI (`CameraView`, Luhn check), colour and passcode.
  - Test first: a scanned 15-digit IMEI that fails the Luhn check is rejected with "That doesn't look like an IMEI".
  - Verify: jest passes.
- **S7.12 Step 3, Problem + promise.**
  - File: `mobile/app/check-in/problem.tsx`: problem chips via `addProblem`, `duePresets`, an optional quote, photos.
  - Test first: the presets render in shop time ("Today 17:00").
  - Verify: jest passes.
- **S7.13 Step 4, Sign + create.**
  - File: `mobile/app/check-in/sign.tsx`: terms, a signature pad, consent toggles, Create.
  - Test first: Create sends one `POST /intake` with an `Idempotency-Key`; success fires `haptics.success()` and shows Print label / Text tracking link.
  - Verify: jest passes.
- **S7.14 Print.**
  - File: `src/features/checkin/printLabel.ts`, downloading `label.pdf` and calling `expo-print` `printAsync({uri})`.
  - Test first: it calls `printAsync` with the downloaded file URI.
  - Verify: jest passes; a real AirPrint label printer is tested by hand.
- **S7.15 Tab bar hidden + "+" entry.**
  - Test first: the `check-in/_layout` hides tabs; the "+" in the Today and Find headers is hidden without `can_create_orders`.
  - Verify: jest passes.
- **S7.16 Offline check-in.**
  - Test first: offline Create is held with the banner "Will check in when back online"; the label is offered after sync.
  - Verify: jest passes.
- **S7.17 E2E.**
  - File: `.maestro/check-in.yaml`: new customer → device → problem → sign → created.
  - Verify: maestro passes, and the elapsed time is logged (< 60 s).

### S8: Push + owner glance
**Backend (B10, B11, B12)**
- **S8.1 `push_devices` migration + model.**
  - Test first: `test_mobile_v1_push.py::test_table`.
  - Verify: alembic, then pytest.
- **S8.2 Register / unregister.**
  - File: `backend/routers/mobile_v1/push.py`.
  - Test first: `test_register_upserts_by_token`, and `test_delete`.
  - Verify: pytest passes.
- **S8.3 Session revoke cascades.**
  - Test first: `test_logout_removes_push_device` (completes F2.6).
  - Verify: pytest passes.
- **S8.4 Native fan-out.**
  - File: `backend/services/push.py` `notify()` → `backend/services/push_expo.py` `send(messages)` (httpx to the Expo push API, batches of 100).
  - Test first: `test_push_expo.py::test_notify_sends_to_native_devices` (httpx mocked); the payload has no phone number and has `data.url`.
  - Verify: pytest passes.
- **S8.5 Receipt pruning.**
  - Test first: `test_device_not_registered_revokes_token`.
  - Verify: pytest passes.
- **S8.6 `job.overdue` sweep.**
  - File: `backend/services/overdue_push.py`, a job every 15 min on the existing scheduler.
  - Test first: `test_overdue_push_once_per_job_shop_tz`.
  - Verify: pytest passes.
- **S8.7 `approval.needed` / `refund.requested` / `part.arrived` kinds.**
  - Test first: one test each in `test_push_kinds.py`, firing from the existing hooks. The approval threshold is `Organisation.approval_push_threshold`, with a migration.
  - Verify: pytest passes.
- **S8.8 `GET /glance`.**
  - File: `backend/routers/mobile_v1/glance.py`.
  - Test first: `test_mobile_v1_glance.py`: `promised_not_ready` counts due-passed jobs not in a ready/done group; `revenue_today` uses the shop-tz day; `Cache-Control: max-age=60`.
  - Verify: pytest passes.
- **S8.9 Fix the company-dashboard UTC day.**
  - File: `backend/routers/reports.py`.
  - Test first: `test_company_dashboard_today_shop_tz.py`.
  - Verify: pytest passes.

**App**
- **S8.10 Permission priming.**
  - File: `src/features/push/Prime.tsx` ("Get a tap when a job needs you", then Turn on / Not now). The OS prompt shows only after Turn on.
  - Test first: "Not now" never calls `requestPermissionsAsync`.
  - Verify: jest passes.
- **S8.11 Register.**
  - File: `src/features/push/register.ts`, calling `getExpoPushTokenAsync` + `getDevicePushTokenAsync` and then `POST /devices/push` on every launch.
  - Test first: it registers once per launch and skips when permission is denied.
  - Verify: jest passes.
- **S8.12 Tap routing.**
  - Test first: a notification response with `data.url = "/go/j/ABC"` routes to the job; a cold start does too (`getLastNotificationResponseAsync`).
  - Verify: jest passes.
- **S8.13 Android channels.**
  - Test first: 4 channels are created (Jobs, Messages, Approvals, Daily summary).
  - Verify: jest passes.
- **S8.14 Notification settings.**
  - File: `mobile/app/(tabs)/more/notifications.tsx`, reading and writing `/api/tech/preferences`, with role defaults.
  - Test first: owner defaults have approvals, refunds and overdue on.
  - Verify: jest passes.
- **S8.15 Glance card.**
  - File: `src/features/glance/GlanceCard.tsx`, with 4 tappable figures; red only when promised-not-ready > 0 and past due.
  - Test first: zero values still show (this is the owner's control panel), and a tap opens the filtered list.
  - Verify: jest passes.
- **S8.16 E2E.**
  - File: `.maestro/push-open.yaml`, simulating a push via `xcrun simctl push` with a payload file; the job opens.
  - Verify: maestro passes.

### S9: More, devices, Face ID, delete account (always last in M1)
- **S9.1 Devices list.**
  - File: `mobile/app/(tabs)/more/devices.tsx`, using B0.2's `/api/auth/sessions`.
  - Test first: "This iPhone" is marked current, and "Sign out other devices" calls `sign-out-others`.
  - Verify: jest passes.
- **S9.2 Face ID lock.**
  - File: `src/features/lock/useAppLock.ts` (the ported `idle.ts` timings + `expo-local-authentication`).
  - Test first: after 10 min in the background with the lock on, `authenticateAsync` is required; on failure the content stays hidden.
  - Verify: jest passes.
- **S9.3 Passcode reveal sheet.**
  - Test first: reveal calls `authenticateAsync` first, then `POST …/passcode/reveal`; the text auto-hides after 30 s.
  - Verify: jest passes.
- **S9.4 Appearance.**
  - Test first: choosing Dark persists and applies; the default stays Light.
  - Verify: jest passes.
- **S9.5 Delete account (B14).**
  - Backend: `backend/routers/mobile_v1/account.py`.
  - Test first: `test_mobile_v1_account.py::test_staff_request_notifies_owner`, and `::test_owner_gets_web_url`.
  - App test first: `more/delete.test.tsx` shows confirm and then the request sent.
  - Verify: pytest + jest pass.
- **S9.6 Public deletion page.**
  - File: `frontend-react/src/public/DeleteAccount.tsx` at `/delete-account`.
  - Test first: `DeleteAccount.test.tsx` renders the form and posts.
  - Verify: vitest passes.
- **S9.7 Force-update screen.**
  - Test first: the `ForceUpdate` screen shows the store button with `store_url`.
  - Verify: jest passes.

### M1 release tasks (after the slices)
- **R1.1 Store listing copy and screenshots** (light mode, real seeded shop data).
- **R1.2 Privacy labels / Data safety form**, following §11.
- **R1.3 Review notes:** login-only enterprise companion; demo shop credentials kept in the store-connect secure notes, not in the repo.
- **R1.4 TestFlight + Play internal testing** with the pilot shop.
- **R1.5 Metrics events.**
  - Files: `POST /api/mobile/v1/events` (batched) and `src/observability/events.ts`.
  - Test first: events are batched every 20 or 30 s and dropped after 1,000 offline.
  - Verify: pytest + jest pass.

### M1 orderings: the pilot picks one

The foundation F is first in both orderings, and S9 + R are last.

| Step | **Tech-first** (techs used phones at the bench) | **Counter/owner-first** (phones stayed at the desk / with the owner) |
|---|---|---|
| 1 | F: Foundation | F: Foundation |
| 2 | **S2** Scan → job + Job screen | **S6** Find by number |
| 3 | **S3** Status + notify | **S2** Job screen (scan comes along, but isn't promoted) |
| 4 | **S4** Photos | **S7** Quick check-in + label |
| 5 | **S1** Today (tech sections) | **S3** Status + notify |
| 6 | **S5** Job timer | **S8** Push + owner glance |
| 7 | **S6** Find | **S1** Today (desk / owner sections) |
| 8 | **S7** Check-in | **S4** Photos |
| 9 | **S8** Push + glance | **S5** Job timer |
| 10 | S9 + R | S9 + R |
| **First internal TestFlight after** | step 4 (scan → status → photo works end to end) | step 4 (find → job → check-in works end to end) |
| **Default tab on launch** | Scan for techs, Today for others | Today for everyone (Find is one tap) |

Why S2 is early in both: the Job screen is the destination of every other flow.

---

## M2: OS surfaces (≈ 2 weeks)

**Day 1 of M2:** request the **Tap to Pay development entitlement** from Apple (account holder, through the developer portal). This is not code; it starts the clock for M3 (spec §7.4).

### M2.1 Widgets (iOS + Android)
- **M2.1.1 Install.**
  - Do: add `expo-widgets` + `@expo/ui` and the config plugin with an App Group `group.com.tekly.go` and `enableAndroid:true`.
  - Test first: `appConfig.test.ts` asserts the plugin and the app group.
  - Verify: jest passes, and a prebuild has a widget target.
- **M2.1.2 Snapshot writer.**
  - File: `src/features/widgets/snapshot.ts`, writing `{glance, myNext3, readyCount, timer}` to shared storage after each board / glance sync and reloading the timelines.
  - Test first: it is written after a sync, with no PII beyond the first name + device.
  - Verify: jest passes.
- **M2.1.3 Owner glance widget.**
  - File: `mobile/widgets/Glance.tsx` (small and medium: the 4 figures, tabular figures, light and dark).
  - Test first: a render test of the pure widget function with a fixture snapshot.
  - Verify: it shows on the simulator home screen.
- **M2.1.4 Tech "Next up" widget.**
  - File: `widgets/NextUp.tsx` (medium: 3 rows, each deep-linking to `/go/j/{code}`).
  - Test first: a fixture render.
  - Verify: on the simulator, a tap opens the job.
- **M2.1.5 Lock Screen widgets.**
  - File: `widgets/LockLate.tsx` (accessoryCircular: late count) and `widgets/LockTimer.tsx` (accessoryRectangular).
  - Verify: added on the simulator lock screen.
- **M2.1.6 Android variants** (subset components).
  - Verify: on the emulator home screen.
- **M2.1.7 Refresh policy.**
  - Test first: the snapshot write is throttled to once a minute.
  - Verify: jest passes.

### M2.2 Live Activities
- **M2.2.1 Timer activity.**
  - File: `widgets/TimerActivity.tsx` (Lock Screen + Dynamic Island: customer, device, elapsed).
  - Test first: starting the timer (S5) calls `startActivity`, and stop ends it.
  - Verify: on a device the Dynamic Island shows the elapsed time.
- **M2.2.2 Push token capture.**
  - Test first: the activity push token is posted to `POST /devices/live-activity`.
  - Verify: jest passes.
- **M2.2.3 Backend B15.**
  - Files: `backend/routers/mobile_v1/live_activity.py`, `backend/services/apns.py` (token-based `.p8`, `apns-push-type: liveactivity`).
  - Test first: `test_live_activity.py::test_status_change_sends_update` (httpx mocked).
  - Verify: pytest passes.
- **M2.2.4 `staleDate`.**
  - Test first: the activity is marked stale 8 h after its last update.
  - Verify: jest passes.

### M2.3 App Shortcuts / Siri (behind the `features.app_intents` flag)
- **M2.3.1 Init.**
  - Do: `npx expo-app-intents init` and pin the exact version.
  - Verify: a prebuild has the intents target.
- **M2.3.2 Intents.**
  - Files: Swift intents for "Scan a label", "Find job" (a string parameter), "Check in a device" and "Clock in", plus their JS handlers in `src/features/intents/handlers.ts` (route to the screens; Clock in calls `/api/timeclock/shift/clock-in`).
  - Test first: the handlers map each intent ID to the right route or call.
  - Verify: the Shortcuts app lists them, and "Hey Siri, find job in Tekly Go" works on a device.
- **M2.3.3 Kill switch.**
  - Test first: `features.app_intents=false` from `/config` makes the handlers no-ops that open the app.
  - Verify: jest passes.
- **M2.3.4 Fallback ready.**
  - Do: document in `mobile/targets/README.md` how to replace the alpha module with a hand-written Swift App Intents target if it breaks.

### M2.4 Share sheet and NFC
- **M2.4.1 Share extension.**
  - Do: "Add to Tekly job…" receives images, shows a job picker (search) and enqueues uploads (the S4 driver).
  - Test first: received image URIs become upload rows with the chosen order.
  - Verify: from Photos on a device.
- **M2.4.2 NFC.**
  - Do: add `react-native-nfc-manager` + plugin. More → "Write tag for a job" writes the universal link; reading in the foreground calls `routeScan`.
  - Test first: an NDEF URI record with `/go/j/ABC` routes to the job.
  - Verify: a background tag read on an iPhone opens the job.

### M2 release
- **R2.1** Store screenshots with widgets.
- **R2.2** Review notes for the widgets and intents.
- **R2.3** Metrics: widget installs (a snapshot-reader ping) and intent invocations.

---

## M3: Tap to Pay (≈ 2 weeks of work, gated on the Apple entitlement)

**Entry criteria:**
- the development entitlement is granted;
- `@stripe/stripe-terminal-react-native` has been re-checked for its current version and preview/GA status;
- the backend Stripe Connect integration supports the Terminal connection tokens and locations the PWA's payment work set up.

- **M3.1 Connection token endpoint.**
  - File: `backend/routers/mobile_v1/terminal.py` `POST /terminal/connection-token`.
  - Test first: `test_terminal.py::test_requires_can_take_payments`, and `::test_returns_secret` (Stripe mocked).
  - Verify: pytest passes.
- **M3.2 Terminal location per shop location.**
  - Test first: `test_location_created_once`.
  - Verify: pytest passes.
- **M3.3 PaymentIntent for a job.**
  - Test first: `test_create_intent_for_balance`: the amount equals `balance_due`, `card_present`, and an Idempotency-Key is used.
  - Verify: pytest passes.
- **M3.4 Webhook → payment recorded.**
  - Test first: `test_succeeded_webhook_posts_payment_once`.
  - Verify: pytest passes.
- **M3.5 SDK + plugin.**
  - Do: add `@stripe/stripe-terminal-react-native` with the plugin `{locationWhenInUsePermission, tapToPayCheck:true, appDelegate:true}` and the entitlement in `app.config.ts`.
  - Test first: `appConfig.test.ts` asserts the entitlement key.
  - Verify: jest passes, and a dev build starts.
- **M3.6 Port `pay.ts`.**
  - Do: copy it with its tests (quick tender, clamp, finish target).
  - Verify: jest passes unchanged.
- **M3.7 "How to tap" education.**
  - Do: `ProximityReaderDiscovery` on iOS 18+; our own screen below that.
  - Test first: it is shown on first use and is reachable from More.
  - Verify: jest passes.
- **M3.8 Pay sheet.**
  - File: `src/features/pay/PaySheet.tsx`: amount (clamped), Face ID check, then collect.
  - Test first: Face ID failure blocks collection; success calls `collectPaymentMethod`, then `confirm`.
  - Verify: jest passes.
- **M3.9 Outcomes.**
  - Test first: declined shows "Card declined", with Try again; success fires `haptics.success()`, refreshes the job and offers "Mark collected" (`finishTarget`).
  - Verify: jest passes.
- **M3.10 Device eligibility.**
  - Test first: an unsupported device or region hides Pay and shows "Use the payment link instead".
  - Verify: jest passes.
- **M3.11 Distribution entitlement request**, after internal testing, with screenshots of the education screen.
- **M3.12 E2E.** Stripe test mode on a physical device with a test card, run by hand and checked off in the release checklist.

---

## Risks and how the plan handles them

| Risk | Mitigation |
|---|---|
| The pilot shows techs won't use phones at the bench | The counter/owner-first ordering; nothing is wasted, because every slice still ships |
| B0.2 (device sessions) never lands | F2 is blocked; fallback is a minimal `auth_sessions` table created in F2.1 (+1 day) |
| `expo-app-intents` alpha breaks | Behind a flag; a Swift target fallback (M2.3.4) |
| Tap to Pay entitlement takes weeks | Requested on M2 day 1; the payment link stays as the fallback |
| Stripe RN SDK stays in preview | M3 entry criterion re-checks it; the web payment link covers the gap |
| Photo loss on app kill | A file-first SQLite queue, re-driven on launch, foreground, reconnect and background task |
| API drift between web and mobile | `/api/mobile/v1` contract snapshots + generated types with CI `--check` |
