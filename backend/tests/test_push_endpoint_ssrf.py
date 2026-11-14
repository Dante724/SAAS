"""Final review SEC-08: a push subscription's endpoint was any URL, so any staff member could make
the server POST to metadata or internal hosts on every notification (blind SSRF; a 404/410 reply
even deleted the row, which leaked a little about the target). Endpoints must now be https on a
known Web Push service, and the address is checked to be public again before each send."""
import socket
import sys
import types

import pytest
from sqlalchemy import select

from backend.models import PushSubscription
from backend.services import push

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("endpoint", [
    "http://169.254.169.254/latest/meta-data/",               # cloud metadata
    "https://10.0.0.5/internal",                              # internal host
    "https://hooks.example.com/x",                            # anything else on the internet
    "http://fcm.googleapis.com/fcm/send/abc",                 # not https
    "https://fcm.googleapis.com.evil.example/fcm/send/abc",   # look-alike host
    "https://evilnotify.windows.com/w/?token=1",              # no dot before the suffix
    "https://fcm.googleapis.com:8443/fcm/send/abc",           # odd port
    "https://user:pw@fcm.googleapis.com/fcm/send/abc",        # credentials
    "fcm.googleapis.com/fcm/send/abc",                        # no scheme
])
async def test_subscribe_refuses_endpoints_that_are_not_push_services(client, tenant, endpoint):
    r = await client.post("/api/push/subscribe", headers=tenant,
                          json={"endpoint": endpoint, "keys": {"p256dh": "k", "auth": "a"}})
    assert r.status_code == 422, r.text


@pytest.mark.parametrize("endpoint", [
    "https://fcm.googleapis.com/fcm/send/abc:APA91b",                       # Chrome, Edge on Android
    "https://updates.push.services.mozilla.com/wpush/v2/gAAAA",             # Firefox
    "https://web.push.apple.com/QGuQyavXutnMH2",                            # Safari, iOS
    "https://wns2-par02p.notify.windows.com/w/?token=BQYAAAB",              # Edge on Windows
])
async def test_subscribe_accepts_the_known_push_services(client, tenant, endpoint):
    r = await client.post("/api/push/subscribe", headers=tenant,
                          json={"endpoint": endpoint, "keys": {"p256dh": "k", "auth": "a"}})
    assert r.status_code == 201, r.text


async def test_push_is_never_sent_to_an_endpoint_that_is_not_a_public_push_service(client, tenant, db, monkeypatch):
    """Rows saved before the check get no POST and are removed; a push host whose DNS answer points
    inside is skipped (the answer can change back, so its row stays)."""
    sent = []
    fake = types.ModuleType("pywebpush")
    fake.webpush = lambda **kw: sent.append(kw["subscription_info"]["endpoint"])
    monkeypatch.setitem(sys.modules, "pywebpush", fake)
    monkeypatch.setattr(push, "push_enabled", lambda: True)
    answers = {"fcm.googleapis.com": "142.250.180.10", "web.push.apple.com": "10.1.2.3"}
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, port, *a, **k: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", (answers.get(host, "127.0.0.1"), port))])
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    legacy = "http://169.254.169.254/latest/meta-data/"
    rebound = "https://web.push.apple.com/rebound"
    good = "https://fcm.googleapis.com/fcm/send/good"
    for ep in (legacy, rebound, good):
        db.add(PushSubscription(org_id=me["org_id"], employee_id=me["id"], endpoint=ep, p256dh="k", auth="a"))
    await db.commit()

    await push._dispatch_push(db, me["id"], {"title": "t"}, commit=True)

    assert sent == [good]
    db.expire_all()
    left = set((await db.execute(select(PushSubscription.endpoint).where(
        PushSubscription.employee_id == me["id"]))).scalars())
    assert legacy not in left, "a row that can never be a push endpoint is removed"
    assert rebound in left and good in left
