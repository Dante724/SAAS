# E2E / visual-regression tests (Playwright)

These are the tests Vitest and pytest **can't** do: they check *appearance* (are the boxes
aligned, did spacing drift) and *rendered text* (no raw `snake_case` key shown where a human
label belongs). They run against a **running app**, not the sandbox.

## One-time install

Playwright is intentionally **not** in `package.json` (keeps CI's `npm ci` lean, since E2E
runs against a live server, not in CI). Install it locally to enable the suite:

```bash
cd frontend-react
npm install
npm i -D @playwright/test
npx playwright install chromium
```

## Run

Point it at your running app and give it sign-in creds (env vars — never commit them):

```bash
BASE_URL=http://127.0.0.1:8000 \
E2E_EMAIL=you@shop.com E2E_PASSWORD=yourpassword \
npm run e2e
```

- **First run:** add `-- --update-snapshots` to record the visual baseline
  (`npm run e2e -- --update-snapshots`). Commit the generated `e2e/**/*.png`.
- **After that:** `npm run e2e` fails if any page's layout shifts beyond ~2% of pixels, or if
  any visible label renders an underscored key.

## What each layer catches

| Layer | Tool | Catches |
|-------|------|---------|
| `npm run lint:uitext` | Node script | raw `snake_case` in **static JSX text** (no browser, runs in CI) |
| `pytest test_seeded_status_names_human` | pytest | any seeded **status name** that's a raw key, all pipelines |
| `npm run e2e` | Playwright | **misaligned boxes / spacing** (screenshot diff) + underscored labels **as rendered** |

Notes:
- The status-key badges in Settings are intentional (`font-mono`, muted) and are excluded from
  the underscore check — those are slugs, not labels.
- `.auth/` and generated screenshots are the only things Playwright writes under `e2e/`.
