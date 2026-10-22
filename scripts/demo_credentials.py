"""Demo-shop passwords for local setup and sales demos (launch QA F10).

The demo seed used to give every account a password printed in the README (admin123/tech123).
Those are public, so they're now on the old-default list and can't sign in. Instead:

- DEMO_ADMIN_PASSWORD / DEMO_STAFF_PASSWORD in the environment win (set them for a stable demo);
- otherwise they're read from `.env.demo` at the repo root (gitignored by `.env.*`);
- otherwise a random pair is generated and written there (mode 600) on first seed.

Look in `.env.demo` for the logins after seeding. Never commit it.
"""
import os
import secrets
from pathlib import Path

DEMO_FILE = Path(__file__).resolve().parents[1] / ".env.demo"
_KEYS = ("DEMO_ADMIN_PASSWORD", "DEMO_STAFF_PASSWORD")


def _read_file() -> dict:
    out = {}
    if DEMO_FILE.exists():
        for line in DEMO_FILE.read_text().splitlines():
            k, sep, v = line.partition("=")
            if sep and k.strip() in _KEYS:
                out[k.strip()] = v.strip()
    return out


def demo_passwords() -> tuple[str, str]:
    """(admin password, staff password) for the demo shop."""
    found = _read_file()
    vals = {k: os.environ.get(k) or found.get(k) for k in _KEYS}
    if not all(vals.values()):
        for k in _KEYS:
            vals[k] = vals[k] or f"demo-{secrets.token_urlsafe(9)}"
        DEMO_FILE.write_text(
            "# Demo-shop logins written by scripts/seed.py (scripts/demo_credentials.py). Never commit.\n"
            "# Admin: admin@repairos.dev  Staff: priya@repairos.dev, amit@repairos.dev\n"
            + "".join(f"{k}={vals[k]}\n" for k in _KEYS))
        try:
            DEMO_FILE.chmod(0o600)
        except OSError:
            pass
    return vals["DEMO_ADMIN_PASSWORD"], vals["DEMO_STAFF_PASSWORD"]
