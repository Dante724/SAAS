"""Batch 2 (Oct 2026 audit) — the work-order list pages on the server with a slim row, the
total counts the filtered rows, and the board only carries open orders, capped, without images."""
import pytest

IMG = "data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw=="


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


async def _order(client, h, loc, cid, **kw):
    r = await client.post("/api/orders", json={"client_id": cid, "location_id": loc,
                                               "complaint": "x", **kw}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


async def _seed(client, h, n_a=5, n_b=3):
    loc = await _loc(client, h)
    a = (await client.post("/api/clients", json={"name": "Alice Pager", "phone": "+15550001"},
                           headers=h)).json()["id"]
    b = (await client.post("/api/clients", json={"name": "Bob Other", "phone": "+15550002"},
                           headers=h)).json()["id"]
    alice = [await _order(client, h, loc, a, asset_brand="Apple", asset_model="iPhone 15")
             for _ in range(n_a)]
    bob = [await _order(client, h, loc, b, asset_brand="Samsung", asset_model="S24")
           for _ in range(n_b)]
    return loc, alice, bob


@pytest.mark.asyncio
async def test_list_pages_with_filtered_total_and_slim_rows(client, tenant):
    _, alice, bob = await _seed(client, tenant)
    r = await client.get("/api/orders?limit=3&skip=0", headers=tenant)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 8 and len(body["items"]) == 3 and body["limit"] == 3
    row = body["items"][0]
    # slim: no lines/comments/history, no inline photo
    for k in ("lines", "comments", "status_logs"):
        assert k not in row
    assert "image_data" not in (row["device"] or {})
    # pages don't overlap and together cover every row once
    seen = []
    for skip in (0, 3, 6):
        seen += [o["id"] for o in (await client.get(f"/api/orders?limit=3&skip={skip}",
                                                    headers=tenant)).json()["items"]]
    assert len(seen) == 8 and len(set(seen)) == 8

    # the total follows the filters (it used to count the whole shop)
    f = (await client.get("/api/orders?search=alice&limit=2", headers=tenant)).json()
    assert f["total"] == 5 and len(f["items"]) == 2
    assert (await client.get("/api/orders?q=samsung", headers=tenant)).json()["total"] == 3
    assert (await client.get(f"/api/orders?search={bob[0]['order_number']}",
                             headers=tenant)).json()["total"] == 1
    assert (await client.get("/api/orders?brand=apple", headers=tenant)).json()["total"] == 5
    assert (await client.get("/api/orders?assigned_to_id=__none", headers=tenant)).json()["total"] == 8
    assert (await client.get("/api/orders?scheduled=yes", headers=tenant)).json()["total"] == 0


@pytest.mark.asyncio
async def test_old_bulk_limit_is_clamped_not_rejected(client, tenant):
    r = await client.get("/api/orders?limit=1000", headers=tenant)
    assert r.status_code == 200, r.text
    assert r.json()["limit"] == 200
    assert (await client.get("/api/orders?limit=100000", headers=tenant)).status_code == 422
    assert (await client.get("/api/orders?skip=-1", headers=tenant)).status_code == 422


@pytest.mark.asyncio
async def test_facets_count_statuses_and_open_jobs(client, tenant):
    _, alice, _ = await _seed(client, tenant, n_a=3, n_b=2)
    r = (await client.get("/api/orders?facets=true&limit=1&search=alice", headers=tenant)).json()
    assert sum(r["status_counts"].values()) == 3          # counts honour the non-status filters
    assert r["open_jobs"] == 5                             # open work orders, shop-wide
    assert isinstance(r["assignees"], list)


@pytest.mark.asyncio
async def test_photo_is_served_by_url_not_inlined(client, tenant):
    loc = await _loc(client, tenant)
    cid = (await client.post("/api/clients", json={"name": "Pic"}, headers=tenant)).json()["id"]
    o = await _order(client, tenant, loc, cid, asset_brand="Apple", asset_model="iPad")
    await client.put(f"/api/assets/{o['device']['id']}", json={"image_data": IMG}, headers=tenant)
    row = (await client.get("/api/orders", headers=tenant)).json()["items"][0]
    url = row["device"]["image_url"]
    assert url == f"/api/assets/{o['device']['id']}/image"
    img = await client.get(url, headers=tenant)
    assert img.status_code == 200 and img.headers["content-type"] == "image/gif"
    assert img.content.startswith(b"GIF89a")


@pytest.mark.asyncio
async def test_board_is_open_only_capped_and_imageless(client, tenant):
    _, alice, bob = await _seed(client, tenant, n_a=4, n_b=1)
    # close one order: it keeps counting but never becomes a card
    st = (await client.get("/api/statuses", headers=tenant)).json()
    st = st.get("items", st) if isinstance(st, dict) else st
    closed = next(s["key"] for s in st if s.get("status_group") == "closed")
    r = await client.patch(f"/api/orders/{bob[0]['id']}/status", json={"status": closed}, headers=tenant)
    assert r.status_code == 200, r.text

    b = (await client.get("/api/orders/board?per_column=2", headers=tenant)).json()
    cards = [c for col in b["columns"] for c in col["cards"]]
    assert bob[0]["id"] not in {c["id"] for c in cards}
    new_col = next(c for c in b["columns"] if c["group"] == "new")
    assert len(new_col["cards"]) == 2 and new_col["count"] == 4 and new_col["truncated"]
    assert b["status_counts"].get(closed) == 1
    assert all("device_image" not in c for c in cards)
    # board filters match the list's
    b2 = (await client.get("/api/orders/board?search=bob", headers=tenant)).json()
    assert sum(len(c["cards"]) for c in b2["columns"]) == 0
