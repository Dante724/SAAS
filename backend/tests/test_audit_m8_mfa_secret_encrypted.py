"""Audit 29 Oct 2026, M8: staff 2-step (TOTP) secrets were stored in plain text, so a leaked
database backup was enough to generate valid codes. They are encrypted with services/crypto, and
rows written before this still work."""
import time

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from backend.config import settings
from backend.models import Employee
from backend.services import crypto
from backend.services.mfa import totp


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())


async def _me(client, h):
    return (await client.get("/api/auth/me", headers=h)).json()


@pytest.mark.asyncio
async def test_enrolment_stores_the_secret_encrypted_and_login_still_works(client, tenant, db, key):
    me = await _me(client, tenant)
    secret = (await client.post("/api/mfa/enroll", headers=tenant)).json()["secret"]
    emp = (await db.execute(select(Employee).where(Employee.id == me["id"]))).scalar_one()
    assert emp.mfa_secret.startswith("enc:v1:") and secret not in emp.mfa_secret
    assert crypto.decrypt(emp.mfa_secret) == secret

    on = await client.post("/api/mfa/activate", json={"code": totp(secret)}, headers=tenant)
    assert on.status_code == 200, on.text
    r = await client.post("/api/auth/token", data={
        "username": me["email"], "password": "pw-test-12345",
        "client_secret": totp(secret, at=int(time.time()) + 30)})
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_plaintext_secret_from_before_still_signs_in(client, tenant, db, key):
    me = await _me(client, tenant)
    secret = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"
    emp = (await db.execute(select(Employee).where(Employee.id == me["id"]))).scalar_one()
    emp.mfa_secret, emp.mfa_enabled = secret, True
    await db.commit()
    r = await client.post("/api/auth/token", data={
        "username": me["email"], "password": "pw-test-12345", "client_secret": totp(secret)})
    assert r.status_code == 200, r.text


def test_migration_encrypts_existing_rows(tmp_path, monkeypatch, key):
    """Run aud8m01's upgrade against a table holding a plaintext secret."""
    import importlib.util

    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    path = next(p for p in __import__("pathlib").Path("backend/migrations/versions").glob("aud8m01_*.py"))
    spec = importlib.util.spec_from_file_location("aud8m01", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    eng = sa.create_engine(f"sqlite:///{tmp_path}/m.db")
    with eng.begin() as conn:
        conn.execute(sa.text("CREATE TABLE employees (id VARCHAR PRIMARY KEY, mfa_secret VARCHAR(64))"))
        conn.execute(sa.text("INSERT INTO employees VALUES ('e1', 'JBSWY3DPEHPK3PXP'), ('e2', NULL)"))
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            mod.upgrade()
        stored = conn.execute(sa.text("SELECT mfa_secret FROM employees WHERE id='e1'")).scalar()
        assert stored.startswith("enc:v1:") and crypto.decrypt(stored) == "JBSWY3DPEHPK3PXP"
        with Operations.context(ctx):
            mod.upgrade()      # running twice doesn't double-encrypt
        assert conn.execute(sa.text("SELECT mfa_secret FROM employees WHERE id='e1'")).scalar() == stored
