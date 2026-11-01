"""Default public/private for new comments and files on orders (Orderry, Jun 2026).

The org setting was stored but nothing read it. New comments and files now follow it unless the
poster picks private/public, and each item can be flipped afterwards.
"""
import pytest

_PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="


async def _order(client, h):
    return (await client.post("/api/orders", json={"complaint": "Screen"}, headers=h)).json()["id"]


async def _post(client, h, oid, **kw):
    r = await client.post(f"/api/feed/order/{oid}", json=kw, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


@pytest.mark.asyncio
async def test_defaults_private_until_the_shop_changes_it(client, tenant):
    oid = await _order(client, tenant)
    assert (await _post(client, tenant, oid, item_type="comment", body="hi"))["visibility"] == "private"

    r = await client.put("/api/staff/company", json={
        "default_comment_visibility": "public", "default_file_visibility": "private"}, headers=tenant)
    assert r.status_code == 200, r.text
    co = (await client.get("/api/staff/company", headers=tenant)).json()
    assert co["default_comment_visibility"] == "public" and co["default_file_visibility"] == "private"

    assert (await _post(client, tenant, oid, item_type="comment", body="ready soon"))["visibility"] == "client"
    assert (await _post(client, tenant, oid, item_type="file", media_data=_PNG,
                        media_name="a.png", media_mime="image/png"))["visibility"] == "private"
    # an explicit choice beats the default
    assert (await _post(client, tenant, oid, item_type="comment", body="internal",
                        visibility="private"))["visibility"] == "private"


@pytest.mark.asyncio
async def test_public_file_default_and_track_page(client, tenant):
    await client.put("/api/staff/company", json={"default_file_visibility": "public"}, headers=tenant)
    oid = await _order(client, tenant)
    f = await _post(client, tenant, oid, item_type="photo", media_data=_PNG, media_name="p.png", media_mime="image/png")
    assert f["visibility"] == "client"
    token = (await client.post(f"/api/orders/{oid}/public-link", headers=tenant)).json()["token"]
    track = (await client.get(f"/api/public/track/{token}")).json()
    assert any(u["type"] == "photo" for u in track["updates"])


@pytest.mark.asyncio
async def test_bad_setting_rejected(client, tenant):
    r = await client.put("/api/staff/company", json={"default_comment_visibility": "everyone"}, headers=tenant)
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_flip_one_item(client, tenant):
    oid = await _order(client, tenant)
    item = await _post(client, tenant, oid, item_type="comment", body="note")
    r = await client.patch(f"/api/feed/item/{item['id']}", json={"visibility": "client"}, headers=tenant)
    assert r.status_code == 200 and r.json()["visibility"] == "client"
    token = (await client.post(f"/api/orders/{oid}/public-link", headers=tenant)).json()["token"]
    track = (await client.get(f"/api/public/track/{token}")).json()
    assert any(u["body"] == "note" for u in track["updates"])

    r = await client.patch(f"/api/feed/item/{item['id']}", json={"visibility": "private"}, headers=tenant)
    assert r.json()["visibility"] == "private"
    track = (await client.get(f"/api/public/track/{token}")).json()
    assert not any(u["body"] == "note" for u in track["updates"])

    assert (await client.patch(f"/api/feed/item/{item['id']}", json={"visibility": "x"},
                               headers=tenant)).status_code == 422


@pytest.mark.asyncio
async def test_system_entries_cannot_be_made_public(client, tenant):
    oid = await _order(client, tenant)
    await client.patch(f"/api/orders/{oid}/status", json={"status": "in_repair"}, headers=tenant)
    items = (await client.get(f"/api/feed/order/{oid}", headers=tenant)).json()["items"]
    sys_item = next(i for i in items if i["item_type"] == "system")
    r = await client.patch(f"/api/feed/item/{sys_item['id']}", json={"visibility": "client"}, headers=tenant)
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_legacy_order_comment_follows_default(client, tenant):
    await client.put("/api/staff/company", json={"default_comment_visibility": "public"}, headers=tenant)
    oid = await _order(client, tenant)
    r = await client.post(f"/api/orders/{oid}/comments", json={"body": "hello"}, headers=tenant)
    assert r.status_code == 201, r.text
    o = (await client.get(f"/api/orders/{oid}", headers=tenant)).json()
    assert o["comments"][-1]["visible_to_client"] is True
