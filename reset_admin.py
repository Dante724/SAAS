"""Create or reset the demo admin, bypassing seed gates. Run: python reset_admin.py"""
import asyncio, json
from sqlalchemy import select
from backend.db.database import engine, AsyncSessionLocal
from backend.models import Base, Organisation, Role, Employee
from backend.services.auth import hash_password

EMAIL, PW, NAME = "admin@repairos.dev", "admin123", "Admin"

async def main():
    from backend.config import settings
    if settings.is_deployed:  # known password = backdoor; dev/demo databases only
        raise SystemExit("Refusing: ENVIRONMENT=production. reset_admin.py is for local/demo databases only.")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSessionLocal() as db:
        org = (await db.execute(select(Organisation).limit(1))).scalar_one_or_none()
        if not org:
            org = Organisation(name="Demo Shop", plan="trial", region="us")
            db.add(org); await db.flush()
        # never block login
        if hasattr(org, "sub_status"): org.sub_status = "active"
        if hasattr(org, "sub_end"): org.sub_end = None
        if hasattr(org, "require_mfa"): org.require_mfa = False
        role = (await db.execute(select(Role).where(Role.org_id == org.id))).scalar_one_or_none()
        if not role:
            role = Role(org_id=org.id, name="Owner", permissions={})
            db.add(role); await db.flush()
        emp = (await db.execute(select(Employee).where(Employee.email == EMAIL))).scalar_one_or_none()
        if not emp:
            emp = Employee(org_id=org.id, role_id=role.id, email=EMAIL, name=NAME,
                           password_hash=hash_password(PW))
            db.add(emp)
        emp.password_hash = hash_password(PW)
        emp.org_id = org.id; emp.role_id = role.id
        emp.active = True
        if hasattr(emp, "email_verified"): emp.email_verified = True
        emp.mfa_enabled = False; emp.mfa_secret = None; emp.mfa_recovery_codes = None
        emp.token_version = (emp.token_version or 0) + 1
        if hasattr(emp, "access_start"): emp.access_start = None
        if hasattr(emp, "access_end"): emp.access_end = None
        await db.commit()
        print(f"OK -> {EMAIL} / {PW}  (org={org.name})")

asyncio.run(main())
