"""Two workers start on a fresh database: both see no platform admin, both insert it. The loser
must step back quietly (no IntegrityError out of ensure_seed_admin) and leave its session usable,
so the startup step after it (built-in admin roles) still runs."""
from sqlalchemy import func, select

from backend.config import settings
from backend.models import PlatformAdmin
from backend.services import platform_auth


async def test_losing_worker_does_not_raise_and_session_stays_usable(db, monkeypatch):
    monkeypatch.setattr(settings, "PLATFORM_ADMIN_EMAIL", "race-admin@platform.local", raising=False)
    monkeypatch.setattr(settings, "PLATFORM_ADMIN_PASSWORD", "Long-enough-pass-123!", raising=False)
    await db.execute(PlatformAdmin.__table__.delete())
    db.add(PlatformAdmin(email="race-admin@platform.local", name="Winner", password_hash="x"))
    await db.commit()

    real_execute = db.execute
    calls = {"n": 0}

    class _NoneResult:
        def scalar_one_or_none(self):
            return None

    async def execute(stmt, *a, **kw):     # the loser read "no admin yet" before the winner committed
        calls["n"] += 1
        if calls["n"] == 1:
            return _NoneResult()
        return await real_execute(stmt, *a, **kw)

    monkeypatch.setattr(db, "execute", execute)
    await platform_auth.ensure_seed_admin(db)          # must not raise
    monkeypatch.setattr(db, "execute", real_execute)
    n = (await db.execute(select(func.count()).select_from(PlatformAdmin))).scalar_one()
    assert n == 1
