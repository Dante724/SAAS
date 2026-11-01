"""Audit fix (144al) — the merge must re-point EVERY client-linked table, not just 6, and undo
must delta-reverse the target balance instead of clobbering post-merge activity.

Before the fix, merging a contact silently orphaned its coupons, gift cards, loyalty, POS sales,
calls, chats, consent log, etc. (data loss on SQLite; IntegrityError 500 on Postgres), and undo
reset the target's balance to its pre-merge snapshot, discarding anything that happened after."""
import pytest


async def _client(client, tenant, **f):
    return (await client.post("/api/clients", json=f, headers=tenant)).json()["id"]


def test_fk_target_set_is_complete():
    """Guard against drift: the discovered set must cover every clients.id FK in the schema."""
    from sqlalchemy import inspect

    import backend.models as m
    from backend.routers.clients import _client_fk_targets

    discovered = {(cls.__name__, attr) for cls, attr in _client_fk_targets()}
    expected = set()
    for name in dir(m):
        cls = getattr(m, name)
        if not isinstance(cls, type) or not hasattr(cls, "__tablename__"):
            continue
        try:
            mapper = inspect(cls)
        except Exception:
            continue
        for col in mapper.columns:
            if any(fk.column.table.name == "clients" for fk in col.foreign_keys):
                expected.add((cls.__name__, col.key))
    # Portal links are revoked on merge/delete, never moved to another contact.
    expected.discard(("ClientPortalLink", "client_id"))
    assert discovered == expected and len(expected) >= 20, (discovered ^ expected)
    # The two FKs the old 6-table list missed must be present.
    assert ("Coupon", "client_id") in discovered
    assert ("Invoice", "payer_client_id") in discovered


@pytest.mark.asyncio
async def test_merge_moves_and_restores_a_previously_orphaned_table(client, tenant):
    target = await _client(client, tenant, name="Keep Co")
    source = await _client(client, tenant, name="Dupe Co")
    # A coupon on the source — a table the old merge did NOT move.
    await client.post(f"/api/clients/{source}/coupons", json={"kind": "pct", "value": 10, "name": "WELCOME"}, headers=tenant)
    assert len((await client.get(f"/api/clients/{source}/coupons", headers=tenant)).json()["items"]) == 1

    m = (await client.post("/api/clients/merge", json={"target_id": target, "source_ids": [source]}, headers=tenant)).json()
    assert m["moved"].get("other", 0) >= 1, m   # the coupon counted under "other records"
    # Coupon now lives on the target, not orphaned.
    assert len((await client.get(f"/api/clients/{target}/coupons", headers=tenant)).json()["items"]) == 1

    # Undo puts the coupon back on the recreated source.
    assert (await client.post(f"/api/clients/merge/{m['merge_log_id']}/undo", headers=tenant)).status_code == 200
    assert len((await client.get(f"/api/clients/{source}/coupons", headers=tenant)).json()["items"]) == 1
    assert len((await client.get(f"/api/clients/{target}/coupons", headers=tenant)).json()["items"]) == 0


@pytest.mark.asyncio
async def test_undo_preserves_post_merge_balance_activity(client, tenant):
    target = await _client(client, tenant, name="Keep Co")
    source = await _client(client, tenant, name="Dupe Co")
    await client.post(f"/api/clients/{source}/balance", json={"kind": "advance", "amount": 100}, headers=tenant)  # source -100

    m = (await client.post("/api/clients/merge", json={"target_id": target, "source_ids": [source]}, headers=tenant)).json()
    assert (await client.get(f"/api/clients/{target}", headers=tenant)).json()["balance"] == -100.0

    # The target does its OWN advance after the merge.
    await client.post(f"/api/clients/{target}/balance", json={"kind": "advance", "amount": 50}, headers=tenant)  # -150
    assert (await client.get(f"/api/clients/{target}", headers=tenant)).json()["balance"] == -150.0

    # Undo must reverse only the merged-in -100, leaving the target's own -50 intact (not reset to 0).
    assert (await client.post(f"/api/clients/merge/{m['merge_log_id']}/undo", headers=tenant)).status_code == 200
    assert (await client.get(f"/api/clients/{target}", headers=tenant)).json()["balance"] == -50.0
    assert (await client.get(f"/api/clients/{source}", headers=tenant)).json()["balance"] == -100.0
