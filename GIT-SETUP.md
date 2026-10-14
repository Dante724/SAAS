# Git & GitHub setup — RepairOS

The repo is already initialised and has one clean commit on branch `main`. These are the exact
steps to back it up / share it on GitHub. **Use a PRIVATE repo — this is production code.**

## 0. Confirm the local state (optional sanity check)

```bash
cd ~/Desktop/repairos
git status           # should say: "On branch main ... nothing to commit, working tree clean"
git log --oneline    # should show the initial commit (f1c580c …)
```

## 1. Create a private GitHub repo and push

### Option A — GitHub CLI (easiest, one command)

If you have the `gh` CLI (`brew install gh`, then `gh auth login` once):

```bash
cd ~/Desktop/repairos
gh repo create repairos --private --source=. --remote=origin --push
```

That creates the private repo, wires up `origin`, and pushes `main` in one go.

### Option B — Manual (create on github.com, then push)

1. Go to https://github.com/new → name it `repairos` → set **Private** → **do NOT** add a README,
   .gitignore, or licence (the repo already has them) → **Create repository**.
2. Copy the repo URL GitHub shows (SSH recommended), then:

```bash
cd ~/Desktop/repairos
git remote add origin git@github.com:YOUR_USERNAME/repairos.git   # or the https:// URL
git push -u origin main
```

## 2. Everyday workflow (how you review "in a diff way" from now on)

```bash
git status                 # what changed
git diff                   # unstaged changes, line by line
git add -A                 # stage everything (respects .gitignore)
git diff --cached          # review exactly what will be committed
git commit -m "message"    # commit
git push                   # send to GitHub
```

## What is and isn't tracked

- **Tracked:** all source (backend, frontend-react, legacy frontend), tests, docs, CI, migrations,
  `.env.example`.
- **Ignored (never committed):** real `.env` files, all local databases (`*.db`, `*.sqlite*`),
  `.venv/` / `venv/`, `node_modules/`, all caches, build output, Playwright auth state.
  See `.gitignore`.

## Safety reminders (production)

- Keep the GitHub repo **private**.
- Never commit a real `.env`, API key, or a production database dump. The `.gitignore` guards
  against the common cases, but double-check `git diff --cached` before any commit that touches
  config.
- If a secret is ever committed by accident, rotate it immediately — removing it from a later
  commit does **not** remove it from history.
