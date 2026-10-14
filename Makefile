.PHONY: install dev run test lint fmt migrate revision seed
install:        ## install runtime + dev deps
	pip install -r requirements-dev.txt
run:            ## run the API (dev)
	uvicorn main:app --reload
test:           ## run the test suite
	pytest -q
lint:           ## lint core + tests
	ruff check main.py backend/config.py backend/core backend/db backend/tests
fmt:            ## auto-fix lint issues
	ruff check --fix .
migrate:        ## apply migrations
	alembic -c backend/alembic.ini upgrade head
revision:       ## autogenerate a migration: make revision m="add x"
	alembic -c backend/alembic.ini revision --autogenerate -m "$(m)"
seed:           ## seed demo data (dev)
	SEED_DEMO=true python -c "import asyncio; from scripts.seed import seed_demo_data; asyncio.run(seed_demo_data())"
