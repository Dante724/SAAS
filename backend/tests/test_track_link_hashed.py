"""SEC-12: customer tracking-link tokens are stored hashed, expire after the job closes, and the
rotate / revoke endpoints keep working (services/track_links)."""
import hashlib
from datetime import timedelta

from sqlalchemy import select, text, update

from backend.models import Order
from backend.services import track_links
from backend.services.dates import now_utc


async def _order(client, h):
    loc = (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]
    cl = (await client.post("/api/clients", json={"name": "Trackee"}, headers=h)).json()["id"]
    r = await client.post("/api/orders", json={"client_id": cl, "location_id": loc, "complaint": "x"}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


async def _row(db, oid):
    return (await db.execute(text("SELECT public_token, public_token_hash, public_token_enc, closed_at "
                                  "FROM orders WHERE id = :i"), {"i": oid})).one()


async def test_the_token_is_stored_as_its_sha256_and_never_in_clear(client, tenant, db):
    o = await _order(client, tenant)
    tok = (await client.get(f"/api/orders/{o['id']}", headers=tenant)).json()["public_token"]
    assert tok
    plain, digest, enc, _closed = await _row(db, o["id"])
    assert plain is None, "the old plaintext column stays empty"
    assert digest == hashlib.sha256(tok.encode()).hexdigest()
    assert enc                                   # kept (encrypted when a key is set) to re-print the link
    assert (await client.get(f"/api/public/track/{tok}")).status_code == 200
    assert (await client.get(f"/api/public/track/{digest}")).status_code == 404   # the hash is not a key


async def test_encrypted_at_rest_when_a_key_is_set(client, tenant, db, monkeypatch):
    from cryptography.fernet import Fernet

    from backend.config import settings
    monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    o = await _order(client, tenant)
    tok = (await client.get(f"/api/orders/{o['id']}", headers=tenant)).json()["public_token"]
    _plain, _digest, enc, _ = await _row(db, o["id"])
    assert tok not in enc and enc.startswith("enc:v1:")
    assert (await client.get(f"/api/public/track/{tok}")).status_code == 200


async def test_rotate_and_revoke_still_work(client, tenant, db):
    o = await _order(client, tenant)
    old = (await client.get(f"/api/orders/{o['id']}", headers=tenant)).json()["public_token"]
    r = await client.post(f"/api/orders/{o['id']}/public-link/rotate", headers=tenant)
    assert r.status_code == 200, r.text
    new = r.json()["token"]
    assert new != old
    assert (await client.get(f"/api/public/track/{old}")).status_code == 404
    assert (await client.get(f"/api/public/track/{new}")).status_code == 200
    assert (await _row(db, o["id"]))[1] == hashlib.sha256(new.encode()).hexdigest()
    r = await client.post(f"/api/orders/{o['id']}/public-link/revoke", headers=tenant)
    assert r.status_code == 200
    assert (await client.get(f"/api/public/track/{new}")).status_code == 404
    assert (await _row(db, o["id"]))[:3] == (None, None, None)


async def test_the_link_expires_180_days_after_the_job_closes(client, tenant, db):
    o = await _order(client, tenant)
    tok = (await client.get(f"/api/orders/{o['id']}", headers=tenant)).json()["public_token"]
    closed = now_utc() - timedelta(days=track_links.TRACK_LINK_DAYS - 1)
    await db.execute(update(Order).where(Order.id == o["id"]).values(closed_at=closed, public_token_issued_at=None))
    await db.commit()
    assert (await client.get(f"/api/public/track/{tok}")).status_code == 200      # day 179: still fine
    await db.execute(update(Order).where(Order.id == o["id"]).values(closed_at=closed - timedelta(days=2)))
    await db.commit()
    r = await client.get(f"/api/public/track/{tok}")
    assert r.status_code == 404 and "expired" in r.json()["detail"]
    assert (await client.get(f"/api/media/track/{tok}/feed/x")).status_code == 404
    # rotating re-issues it for another 180 days
    new = (await client.post(f"/api/orders/{o['id']}/public-link/rotate", headers=tenant)).json()["token"]
    assert (await client.get(f"/api/public/track/{new}")).status_code == 200
    # reopening the job (closed_at cleared) keeps the link alive however old the close was
    await db.execute(update(Order).where(Order.id == o["id"]).values(closed_at=None, public_token_issued_at=None))
    await db.commit()
    assert (await client.get(f"/api/public/track/{new}")).status_code == 200


async def test_a_link_the_previous_image_issued_in_clear_still_opens(client, tenant, db):
    """During a deploy (or after a rollback) the old code writes orders.public_token in clear and no
    hash. The new code still finds that link."""
    o = await _order(client, tenant)
    await db.execute(text("UPDATE orders SET public_token = 'legacy-plain-token-123', public_token_hash = NULL, "
                          "public_token_enc = NULL WHERE id = :i"), {"i": o["id"]})
    await db.commit()
    assert (await client.get("/api/public/track/legacy-plain-token-123")).status_code == 200
    row = (await db.execute(select(Order).where(Order.public_token == "legacy-plain-token-123")
                            .execution_options(skip_tenant_filter=True))).scalar_one()
    assert row.public_token == "legacy-plain-token-123"
