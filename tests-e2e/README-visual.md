# Visual check (pixel baselines)

`visual.spec.js` takes one screenshot of every staff screen, at desktop (1440) and phone (390) width.
It compares each one pixel by pixel with its baseline in `visual.spec.js-snapshots/`. It runs in the
**E2E** workflow on every pull request, first, right after the demo shop is seeded, before the
functional and tab-shift checks change any data.

A run fails when any pixel differs from the baseline by more than a faint anti-aliasing shade.
That is strict on purpose: a button's corners going from 12px to 8px moves only 6–13 pixels on a
screen. When it fails, the run's **visual-diffs** artifact has an expected, an actual and a diff
image for each screen that changed.

## Is the change meant?

- **No:** fix the code. The diff image shows where.
- **Yes** (a design change, a new button, new text): update the baselines on purpose, in the same
  pull request, so the reviewer sees the new pictures next to the code.

## Updating baselines on purpose

Baselines are recorded by Chromium on Linux with the Playwright version pinned in `package.json`.
Use either way below. Don't record them on a Mac or Windows machine: fonts render differently
there, and those files would never match CI.

### From CI (recommended)

1. Actions → **E2E (Technician PWA)** → **Run workflow**. Pick your branch and tick
   **Record new visual baselines**.
2. When the run finishes, download its **visual-baselines** artifact.
3. Replace `tests-e2e/visual.spec.js-snapshots/` with the artifact's files. Check the changed PNGs in
   the diff (only the screens you meant to change should differ), then commit them.

### On a Linux machine

Start the app on a **freshly seeded** demo shop. Data and saved preferences that other runs left
behind (for example, a report you opened last) show up in the pictures.

```bash
rm -f repairos.db && SEED_DEMO=true ENVIRONMENT=development SECRET_KEY=<32+ chars> python3 -m uvicorn main:app --port 8000
cd tests-e2e && npm install && npx playwright install chromium
npx playwright test visual.spec.js --project=mobile --update-snapshots
```

Then check and commit the changed PNGs, as above.

## Why it stays steady

- The browser clock is fixed (14 January 2026), so screens that open on "today" show the same day.
- Dates, times, weekday names and countdowns ("5h left", "2 days ago") are replaced with `##`
  before each shot, because the demo seed creates its records relative to the real date.
- Animations and the text caret are off, the time zone is UTC, and each shot waits for the network
  to go quiet and the fonts to load.
- All the specs share one sign-in (`admin-token.js`), so the run stays under the server's limit of 10
  sign-ins per 5 minutes.

## Adding a screen

Add a `[name, path]` pair to `SCREENS` in `visual.spec.js`, then record its baselines as above.
