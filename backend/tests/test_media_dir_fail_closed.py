"""Beta blocker — a deployed server must not keep uploads under /tmp: they vanish on the next
container restart. Local development keeps the /tmp default."""
import pytest
from cryptography.fernet import Fernet

from backend.config import Settings

DEPLOYED = dict(ENVIRONMENT="production", SECRET_KEY="s" * 48, CORS_ORIGINS="https://app.example.com",
                DATABASE_URL="postgresql+asyncpg://u@db/tekly", PUBLIC_BASE_URL="https://app.example.com",
                SMTP_HOST="smtp.example.com", SMTP_FROM="no-reply@example.com",
                TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode())


@pytest.mark.parametrize("env", ["production", "staging"])
@pytest.mark.parametrize("path", ["/tmp/repairos-media", "/tmp", "/tmp/../tmp/m", "/var/tmp/media"])
def test_deployed_refuses_tmp_media_dir(env, path):
    with pytest.raises(ValueError) as e:
        Settings(_env_file=None, **{**DEPLOYED, "ENVIRONMENT": env},
                 MEDIA_BACKEND="local", MEDIA_LOCAL_DIR=path)
    msg = str(e.value)
    assert "MEDIA_LOCAL_DIR" in msg and "MEDIA_BACKEND=s3" in msg


def test_deployed_allows_a_durable_local_dir():
    s = Settings(_env_file=None, **DEPLOYED, MEDIA_BACKEND="local", MEDIA_LOCAL_DIR="/var/lib/tekly/media")
    assert s.MEDIA_LOCAL_DIR == "/var/lib/tekly/media"


def test_deployed_db_and_s3_backends_ignore_the_dir():
    Settings(_env_file=None, **DEPLOYED)                       # default db backend still boots
    Settings(_env_file=None, **DEPLOYED, MEDIA_BACKEND="s3", MEDIA_S3_BUCKET="b")


def test_local_dev_keeps_tmp_default():
    s = Settings(_env_file=None, ENVIRONMENT="development", MEDIA_BACKEND="local")
    assert s.MEDIA_LOCAL_DIR.startswith("/tmp")
