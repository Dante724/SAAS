# RepairOS — Orderry-style Repair Shop Manager

Full-stack repair shop management app. Python/FastAPI backend + Liquid-Glass × Claymorphism frontend (no build step).

## Run it (VS Code or terminal)

```bash
cd repairos

# 1. Virtual environment
python -m venv .venv
source .venv/bin/activate        # Mac/Linux
.venv\Scripts\activate           # Windows

# 2. Install
pip install -r requirements.txt

# 3. Launch
uvicorn main:app --reload --port 8000
```

Open **http://localhost:8000**

### Demo logins
- Admin:  `admin@repairos.dev` / `admin123`
- Tech:   `priya@repairos.dev` / `tech123`

The database (`repairos.db`) auto-creates and seeds demo data on first launch.

## VS Code
Open the folder, press **F5**, choose "Run RepairOS". Install the Python extension if prompted.

## API docs
Interactive Swagger UI at **http://localhost:8000/docs**

## Stack
| Layer    | Tech |
|----------|------|
| Backend  | FastAPI + SQLAlchemy 2 (async) |
| Database | SQLite (swap to Postgres via `.env`) |
| Auth     | JWT + bcrypt |
| Frontend | Vanilla HTML/CSS/JS — Liquid Glass × Claymorphism design system |

## Structure
```
repairos/
├── main.py                       FastAPI app + startup/seed
├── requirements.txt
├── backend/
│   ├── config.py
│   ├── db/database.py            Async engine + session
│   ├── models/__init__.py        25 SQLAlchemy tables
│   ├── routers/                  auth, orders, clients, inventory,
│   │                             finance, staff, reports
│   └── services/auth.py          JWT + password hashing
├── frontend/
│   ├── index.html                App shell
│   └── static/
│       ├── css/design.css        Design system (glass + clay tokens)
│       ├── css/shell.css         Sidebar, topbar, auth
│       └── js/
│           ├── core.js           API client, toast, modal, formatters
│           ├── app.js            Router + page controller
│           └── pages/            dashboard, orders, clients,
│                                 inventory, other (finance/tasks/staff/reports)
└── scripts/seed.py               Demo data
```

## Switch to Postgres
Edit `.env`:
```
DATABASE_URL=postgresql+asyncpg://user:pass@localhost/repairos
```
Then `pip install asyncpg`.
