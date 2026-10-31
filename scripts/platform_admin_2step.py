"""Turn on 2-step sign-in for an operator (platform admin) account, from the server.

On a live server the /admin portal won't set up 2-step itself: a session signed in with only the
password could otherwise enrol an attacker's phone and lock the real admin out. Run this where the
app runs (it uses the app's DATABASE_URL and TOKEN_ENCRYPTION_KEY):

    python -m scripts.platform_admin_2step ops@yourco.com

It prints a key to add to an authenticator app, asks for the code the app shows, then turns 2-step
on and signs the account out everywhere. To change phones, turn 2-step off in the portal (password
and a current code) and run this again.
"""
import asyncio
import sys

from sqlalchemy import select


async def run(email: str) -> int:
    from backend.db.database import AsyncSessionLocal
    from backend.models import PlatformAdmin
    from backend.services.platform_auth import begin_two_step, finish_two_step

    async with AsyncSessionLocal() as db:
        admin = (await db.execute(select(PlatformAdmin).where(
            PlatformAdmin.email == email.strip().lower()))).scalar_one_or_none()
        if not admin:
            print(f"No operator account for {email}.")
            return 1
        if admin.mfa_enabled:
            print("2-step sign-in is already on for this account.")
            return 1
        secret, uri = begin_two_step(admin)
        print("Add this account to your authenticator app.")
        print(f"Key: {secret}")
        print(f"Link: {uri}")
        code = input("Code shown in the app: ")
        if not await finish_two_step(db, admin, code):
            await db.rollback()
            print("That code didn't work. Nothing was changed; run this again.")
            return 1
        await db.commit()
        import logging
        logging.getLogger("tekly.audit.platform").info(
            "platform audit: platform.mfa_enable by %s via server script", admin.id)
        print("2-step sign-in is on. Every existing session for this account was signed out.")
        return 0


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("Usage: python -m scripts.platform_admin_2step <operator email>")
    sys.exit(asyncio.run(run(sys.argv[1])))


if __name__ == "__main__":
    main()
