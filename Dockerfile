# RepairOS production image: builds the React app, then serves it and the API from one
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
RUN npm run build

# ── 2. Python runtime ────────────────────────────────────────────────────────
FROM python:3.11-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    ENVIRONMENT=production \
    LOG_JSON=true \
    WEB_CONCURRENCY=2 \
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
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"

# --proxy-headers + FORWARDED_ALLOW_IPS: trust X-Forwarded-For only from your load balancer
# (set FORWARDED_ALLOW_IPS to its address/CIDR). Rate limits then see real client IPs.
CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port 8000 --workers ${WEB_CONCURRENCY} --proxy-headers --forwarded-allow-ips ${FORWARDED_ALLOW_IPS}"]
