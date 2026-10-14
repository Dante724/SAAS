.PHONY: install dev run test test-pg lint fmt migrate revision seed ci ci-backend ci-frontend
# Same env CI uses (see .github/workflows/ci.yml) so local results match CI.
CI_ENV = ENVIRONMENT=test SEED_DEMO=false DATABASE_URL=sqlite+aiosqlite:///./ci_test.db

install:        ## install runtime + dev deps (pinned — same versions as CI)
	pip install -r requirements-dev.txt
run:            ## run the API (dev)
	uvicorn main:app --reload
test:           ## run the backend test suite (parallel)
	pytest -q -n auto
test-pg:        ## run the suite against Postgres: make test-pg PG=postgresql+asyncpg://user:pass@localhost:5432/repairos_test
	TEST_DATABASE_URL=$(PG) DATABASE_URL=$(PG) ENVIRONMENT=test SEED_DEMO=false pytest -q -p no:xdist
lint:           ## lint core + tests (pinned ruff)
	ruff check main.py backend/config.py backend/core backend/db backend/tests
fmt:            ## auto-fix lint issues (safe fixes only)
	ruff check --fix main.py backend/config.py backend/core backend/db backend/tests
migrate:        ## apply migrations
	alembic -c backend/alembic.ini upgrade head
revision:       ## autogenerate a migration: make revision m="add x"
	alembic -c backend/alembic.ini revision --autogenerate -m "$(m)"
seed:           ## seed demo data (dev)
	SEED_DEMO=true python -c "import asyncio; from scripts.seed import seed_demo_data; asyncio.run(seed_demo_data())"

ci: ci-backend ci-frontend   ## run EVERY CI gate locally, in CI order. Green here == green in CI.
	@echo "✓ all CI gates passed"

ci-backend:     ## backend gates: lint → migrations from scratch → tests
	ruff check main.py backend/config.py backend/core backend/db backend/tests
	rm -f ci_test*.db && $(CI_ENV) alembic -c backend/alembic.ini upgrade head
	rm -f ci_test*.db && $(CI_ENV) pytest -q -n auto
	rm -f ci_test*.db

ci-frontend:    ## frontend gates: typecheck → ui-text lint → unit tests → build
	cd frontend-react && npx tsc --noEmit && npm run lint:uitext && npm test && npm run build
