"""Read-only audit: list active staff accounts that still use the old default password.

Why: until this fix, every employee created from the React Settings screen got the password
"changeme123" (the form never sent one). Anyone who knew or guessed such an employee's email
could sign in. Those accounts stay exposed until their password is changed.

SAFE ON PRODUCTION: only SELECTs; on Postgres the transaction is READ ONLY; always rolled back.
Nothing is changed — decide per account whether to email a reset link (Settings → Team) or
disable it.

Usage (uses the app's DATABASE_URL):
    DATABASE_URL=... python scripts/find_default_passwords.py
Exit code: 0 = none found, 1 = accounts found (listed), 2 = could not run.
"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.disable(logging.CRITICAL)

from sqlalchemy import select, text  # noqa: E402

OLD_DEFAULTS = ("changeme123",)


async def main() -> int:
    from backend.db.database import AsyncSessionLocal
    from backend.models import Employee, Organisation
    from backend.services.auth import verify_password

    try:
        async with AsyncSessionLocal() as db:
            if db.bind.dialect.name == "postgresql":
                await db.execute(text("SET TRANSACTION READ ONLY"))
            rows = (await db.execute(
                select(Employee.email, Employee.password_hash, Organisation.name)
                .join(Organisation, Organisation.id == Employee.org_id)
                .where(Employee.active == True)  # noqa: E712
                .order_by(Organisation.name, Employee.email))).all()
            await db.rollback()
    except Exception as e:  # noqa: BLE001
        print(f"Could not query the database: {type(e).__name__}: {e}")
        return 2

    hits = [(org, email) for email, pw_hash, org in rows
            if any(verify_password(p, pw_hash) for p in OLD_DEFAULTS)]
    print(f"Checked {len(rows)} active staff accounts.")
    if not hits:
        print("None use the old default password.")
        return 0
    print(f"{len(hits)} still use the old default password:")
    for org, email in hits:
        print(f"  {org}: {email}")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
