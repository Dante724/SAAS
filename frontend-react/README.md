# RepairOS React frontend (Strangler Fig migration — Option B)

New React UI that grows **alongside** the existing vanilla app. Backend, schema, and
data are **unchanged** — this calls the same FastAPI endpoints at `/api`. Nothing here
modifies the live app; routes flip from legacy → React one module at a time.

## Run (dev)

```bash
cd frontend-react
npm install
npm run dev          # http://localhost:5175/app2/  (proxies /api → localhost:8000)
```

Make sure the FastAPI backend is running on `localhost:8000` and you're logged in there
(the dev proxy forwards `/api` with your session).

## Build + serve from FastAPI (parity testing at /app2)

```bash
npm run build        # → frontend-react/dist
```

Then mount the build in the backend so it's same-origin (session/cookies shared),
without touching the live app served at `/`:

```python
# in main.py (add once, near the other static mounts)
from fastapi.staticfiles import StaticFiles
import os
_dist = os.path.join(os.path.dirname(__file__), "frontend-react", "dist")
if os.path.isdir(_dist):
    # SPA: serve assets + index.html fallback under /app2
    app.mount("/app2", StaticFiles(directory=_dist, html=True), name="react-preview")
```

Open `http://localhost:8000/app2/` — the React Work-orders preview loads with real data.
The production vanilla app at `http://localhost:8000/` is unaffected.

## Migration model

- `src/App.tsx` holds `ROUTE_OWNERS` — the scoreboard of `legacy` vs `react` per module.
- A module flips to `react` only after it passes the **parity gate** in the PM plan
  (`RepairOS-ReactMigration-PMPlan.md`): every feature/flow works, same endpoints,
  RBAC, states, Stitch look, verified live vs legacy.
- Design tokens live in `tailwind.config.js` (ported from `repairos-v2.css`) so the
  Stitch look is consistent from day one.

## Structure

```
frontend-react/
├── index.html
├── vite.config.ts         # base /app2, dev proxy /api → :8000
├── tailwind.config.js     # Stitch design tokens
├── src/
│   ├── main.tsx           # BrowserRouter basename /app2
│   ├── App.tsx            # routes + ROUTE_OWNERS scoreboard
│   ├── index.css          # tokens + primitives (.pill/.card/.btn-*)
│   ├── api/client.ts      # session-reusing fetch wrapper
│   └── pages/
│       └── WorkOrdersPreview.tsx   # Sprint 0 proof — real /api/orders
```

## Next (Sprint 1)

Port the already-built WO React components (WorkOrdersTableHub, the 5 drawer tabs,
create drawer, Client/Asset right-slide drawers) into `src/pages/orders/` and reach
feature parity, then flip `/orders` (and later `/clients`, `/assets`) to `react`.
