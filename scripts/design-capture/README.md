# Design capture

Before / after numbers for design PRs. `capture.js` opens every staff screen of a running app,
and each tab on it, plus a record drawer (and each of its tabs) on Work orders, Inventory, Contacts and Invoices. It
checks each one in light and dark, at desktop (1440) and phone (390) widths. It changes nothing
in the app.

| Measure | What counts |
|---|---|
| Text below AA contrast (light, dark) | Visible, enabled text under 4.5:1 against its real background, or under 3:1 for large text (24px+, or 18.66px+ bold). Disabled controls and rows marked `aria-disabled` are exempt, as WCAG allows. Text that only shows on hover is skipped. |
| Off-design buttons | Filled or outlined buttons that don't look like the shared `<Button>`: corners other than 12px (a split button may square off its inner side), a height other than 32 / 36 / 44px, or text lighter than semibold. Tabs, switches, menus, segmented controls, pills and circles (chips, tags, avatars), fields and clickable cards are left out. |
| Sideways scroll on a phone | Screens where the page or its main column is wider than a 390px screen. |

A "spot" is one element on one screen or tab. An element that fails at both widths counts once.
A sidebar or header element counts once however many screens show it.

## Run

```bash
cd tests-e2e && npm install            # Playwright, once
cd frontend-react && npm ci && npm run build
SEED_DEMO=true ENVIRONMENT=development SECRET_KEY=<32+ chars> python3 -m uvicorn main:app --port 8000
node scripts/design-capture/capture.js
```

- `BASE_URL=http://127.0.0.1:8111` points it at another server, for example an older build for the "before" numbers.
- `--only=/orders,/settings` checks only those screens.
- `--out=<dir>` sets where the details go (default `design-capture-out/`, which git ignores).
- `--jobs=<n>` sets how many pages are open at once (default 3).

Sign-in uses the demo admin: `E2E_ADMIN_EMAIL` and `E2E_ADMIN_PASS`, or the `.env.demo` file the
demo seed writes. The app's rate limits can trip during a run. If they do, start the server with
`RATE_LIMIT_USER_PER_MIN=100000 RATE_LIMIT_IP_PER_MIN=100000`.

The summary prints the four numbers. `summary.json`, `contrast-light.json`, `contrast-dark.json`
and `buttons.json` list each spot with its screen, text, colours, ratio and classes.
