"""Beta blocker — users need a way to reach support: the app gets the configured
SUPPORT_EMAIL / SUPPORT_URL from /api/auth/me, and errors that say "contact support" name it."""
from backend.config import Settings, settings


def test_support_defaults():
    s = Settings(_env_file=None, ENVIRONMENT="development")
    assert s.SUPPORT_EMAIL == "hello@tekly.app" and s.SUPPORT_URL == ""


async def test_me_carries_support_contact(client, tenant, monkeypatch):
    r = await client.get("/api/auth/me", headers=tenant)
    assert r.json()["support"] == {"email": "hello@tekly.app", "url": ""}
    monkeypatch.setattr(settings, "SUPPORT_EMAIL", "help@shop.example")
    monkeypatch.setattr(settings, "SUPPORT_URL", "https://help.example/tekly")
    r = await client.get("/api/auth/me", headers=tenant)
    assert r.json()["support"] == {"email": "help@shop.example", "url": "https://help.example/tekly"}


def test_contact_support_messages_name_the_address(monkeypatch):
    from backend.services import auth
    monkeypatch.setattr(settings, "SUPPORT_EMAIL", "help@shop.example")
    assert "help@shop.example" in auth.subscription_ended_message()
    assert "help@shop.example" in auth.account_off_message()
