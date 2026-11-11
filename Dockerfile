# Tekly production image: builds the React app, then serves it and the API from one
# uvicorn process group. Build:  docker build -t repairos .
# Run:    docker run -p 8000:8000 --env-file .env repairos
# Schema is owned by Alembic in production — run `alembic -c backend/alembic.ini upgrade head`
# (e.g. as a release/pre-deploy step) before starting new code.

# ── 1. Frontend build ────────────────────────────────────────────────────────
FROM node:22-bookworm-slim AS frontend
WORKDIR /app/frontend-react
COPY frontend-react/package.json frontend-react/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend-react/ ./
# Optional browser error tracking, baked in at build time (a frontend DSN is public by design):
#   docker build --build-arg VITE_SENTRY_DSN=https://...  --build-arg VITE_RELEASE=$(git rev-parse HEAD)
ARG VITE_SENTRY_DSN=""
ARG VITE_RELEASE=""
RUN npm run build

# ── 2. Python runtime ────────────────────────────────────────────────────────
FROM python:3.11-slim-bookworm AS runtime
# One worker by default: rate limits are per process unless REDIS_URL is set. Deployed, the app
# refuses to boot without REDIS_URL (or SINGLE_WORKER=true: one worker, one instance), and with
# FORWARDED_ALLOW_IPS left at 127.0.0.1 unless TRUSTED_PROXY_HOPS or NO_REVERSE_PROXY=true is set
# (backend/config.py, DEPLOY.md).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    ENVIRONMENT=production \
    LOG_JSON=true \
    WEB_CONCURRENCY=1 \
    FORWARDED_ALLOW_IPS=127.0.0.1
WORKDIR /app

# Hash-pinned dependencies (requirements.lock is generated from requirements.txt by uv).
COPY requirements.lock ./
RUN pip install --require-hashes -r requirements.lock

COPY main.py reset_admin.py ./
COPY backend/ backend/
COPY frontend/ frontend/
COPY scripts/ scripts/
COPY --from=frontend /app/frontend-react/dist frontend-react/dist

RUN useradd --system --uid 10001 --no-create-home repairos && chown -R repairos /app
USER repairos

EXPOSE 8000
# Liveness only: /api/health never touches the database, so a DB blip doesn't restart every
# container at once. Readiness (database reachable) is /api/readyz, for load balancers.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"

# --proxy-headers + FORWARDED_ALLOW_IPS: trust X-Forwarded-For only from your load balancer
# (set FORWARDED_ALLOW_IPS to its address/CIDR). Rate limits then see real client IPs.
CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port 8000 --workers ${WEB_CONCURRENCY} --proxy-headers --forwarded-allow-ips ${FORWARDED_ALLOW_IPS}"]
