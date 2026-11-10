# Design capture: screenshots, tab-shift and contrast checks

These are the scripts behind the UI design audit. They screenshot every screen and measure what moves when tabs switch and which text misses contrast. Run them before and after a design change and compare the numbers. The CI guards are separate: `npm run lint:design` and `tests-e2e/tab-shift.spec.js`.

## Setup
1. Start the app with the demo shop on its own port and database. Use a stable admin password so the script can sign in:
   ```bash
   ENVIRONMENT=development SEED_DEMO=true DEMO_ADMIN_PASSWORD=<test password> \
     RATE_LIMIT_IP_PER_MIN=100000 RATE_LIMIT_USER_PER_MIN=100000 \
     DATABASE_URL=sqlite+aiosqlite:///./design.db python -m uvicorn main:app --port 8111
   ```
   The React build must exist (`cd frontend-react && npm ci && npm run build`).
2. Install the browser driver: `cd scripts/design-capture && npm install && npx playwright-core install chromium`.
3. Get a sign-in token: `curl -s -X POST localhost:8111/api/auth/token -d 'username=admin@repairos.dev&password=<test password>' | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])' > token`.
4. Create `tokens.json` for the public pages. Values come from the demo database. Make a check-in point and a portal link through the API first, because the seed has neither.
   ```json
   {"track":"<order public_token>","portal":"<client portal token>","checkin":"<checkin point token>","org":"<org id>","order":"<order id>"}
   ```
5. Optional, for the admin panel: `admin_cookies.json` with a `tekly_admin` session cookie (Playwright cookie format). The local dev admin is `admin@platform.local` and needs 2-step setup.

## Run
```bash
export OUT=$PWD/out TOKEN_FILE=$PWD/token TOKENS=$PWD/tokens.json   # ADMIN_COOKIES=$PWD/admin_cookies.json
node capture.mjs                       # 66 screens × 1440/390 × light/dark; every tab, scroll step and the first drawers
ONLY='^settings-' CFG=d-light node capture.mjs   # a subset: page-id regex and view names
python3 analyze.py $OUT                # writes $OUT/data/{tab_shift,coverage,metrics_summary}.json and prints a digest
python3 compare.py <before dir> <after dir> [phase]   # before/after table for the audit's tab-shift rows (needs <before>/council/plan.json)
```

- Screenshots land in `$OUT/screens`. They are about 80 MB per full run, so don't commit them.
- `records.jsonl` holds the raw measurements: button sizes, touch targets under 44px, contrast failures, truncation, empty states and tab-switch positions.
- `build_report.py` turned the council's `plan.json` into the owner's audit page.

## Reading the numbers
- **Tab shift:** a row in `tab_shift.json` reports how far the title, tab bar, tabs and toolbar controls moved between tabs. `autoscrolled` rows are already normalised.
  - Content that differs per tab (a board vs a list) shows up as `first_content` movement. Check those in the screenshots.
  - The CI check `tests-e2e/tab-shift.spec.js` measures only the controls, and is the gate.
- **Contrast:** text below 4.5:1 (3:1 for large text). Known false positives: tinted badges using `color(srgb …)` backgrounds, which are reported as about 1.14:1.
- **Touch targets:** elements under 44px on phone. Elements with the `.hit44` touch area (Button, Switch, ScanButton) are counted by their visible size even though their tap area is 44px.
