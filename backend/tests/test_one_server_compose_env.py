"""deploy/one-server/compose.yml: every container that runs the Tekly image boots its settings.

backend/migrations/env.py imports backend.config.settings, so the `migrate` service validates the
same deployed-mode rules as the app (proxy trust, REDIS_URL...). It used to get only .env plus
DATABASE_URL, died at Settings() with "Proxy trust is not configured", and the app never started.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
COMPOSE = REPO / "deploy" / "one-server" / "compose.yml"
APP_ENV_KEYS = {"DATABASE_URL", "REDIS_URL", "FORWARDED_ALLOW_IPS"}

# What .github/workflows/one-server.yml writes to .env (placeholder values, nothing real).
CI_DOTENV = {
    "ENVIRONMENT": "staging",
    "PUBLIC_BASE_URL": "http://localhost:8000",
    "CORS_ORIGINS": "http://localhost:8000",
    "SECRET_KEY": "s" * 64,
    "TOKEN_ENCRYPTION_KEY": "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=",
    "POSTGRES_PASSWORD": "ci-password",
    "CLOUDFLARE_TUNNEL_TOKEN": "unused-in-ci",
    "MEDIA_BACKEND": "db",
    "SMTP_HOST": "smtp.invalid",
    "SMTP_FROM": "ci@tekly.invalid",
    "STRIPE_BILLING_SECRET_KEY": "sk_test_ci_placeholder",
    "STRIPE_BILLING_WEBHOOK_SECRET": "whsec_ci_placeholder",
    **{f"STRIPE_BILLING_PRICE_{p}_{t}": f"price_ci_{p}_{t}"
       for p in ("SOLO", "SHOP", "PRO", "SHOP_SEAT") for t in ("MONTH", "YEAR")},
}


def _services():
    return yaml.safe_load(COMPOSE.read_text())["services"]      # anchors and << merges resolved


def _tekly_services(services):
    return {name: s for name, s in services.items() if "TEKLY_IMAGE" in str(s.get("image", ""))}


def _interpolate(value, env):
    """Compose's ${VAR}, ${VAR:-default} and ${VAR:?error}, enough for this file."""
    def sub(m):
        name, op, arg = m.group(1), m.group(2), m.group(3)
        if env.get(name):
            return env[name]
        assert op != ":?", f"compose would refuse: {arg}"
        return arg or ""
    return re.sub(r"\$\{(\w+)(?:(:-|:\?)([^}]*))?\}", sub, str(value))


def test_every_tekly_container_gets_the_app_env():
    tekly = _tekly_services(_services())
    assert {"migrate", "app"} <= set(tekly)
    for name, svc in tekly.items():
        missing = APP_ENV_KEYS - set(svc.get("environment") or {})
        assert not missing, f"compose service {name!r} runs the Tekly image without {sorted(missing)}"
    values = [{k: tekly[n]["environment"][k] for k in APP_ENV_KEYS} for n in tekly]
    assert all(v == values[0] for v in values), "the Tekly containers disagree on the shared app env"
    assert "NO_REVERSE_PROXY" not in str(tekly)          # the tunnel is a real proxy in front


def test_settings_build_with_the_migrate_service_env(tmp_path):
    """Settings() as the migrate container builds it: CI's .env plus the service's environment."""
    env = dict(CI_DOTENV)
    env.update({k: _interpolate(v, CI_DOTENV) for k, v in _services()["migrate"]["environment"].items()})
    env.update(PATH=os.environ.get("PATH", ""), PYTHONPATH=str(REPO))
    r = subprocess.run([sys.executable, "-c", "from backend.config import settings; print(settings.ENVIRONMENT)"],
                       cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)  # cwd: no stray .env
    assert r.returncode == 0, r.stderr[-2000:]
    assert r.stdout.strip() == "staging"
