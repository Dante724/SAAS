"""Kanban board endpoint — regression for the null-device 500 (build 144)."""
import pytest


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


@pytest.mark.asyncio
async def test_board_seeds_columns_even_before_statuses_page(client, tenant):
    # a freshly registered org has never opened the statuses page; the board
    # must still return its six status-group columns (self-seeding).
    b = await client.get("/api/orders/board?is_estimate=false", headers=tenant)
    assert b.status_code == 200, b.text
    groups = [c["group"] for c in b.json()["columns"]]
    assert groups == ["new", "in_progress", "pending", "ready", "closed", "dropped"]


@pytest.mark.asyncio
async def test_board_survives_device_with_no_brand_or_model(client, tenant):
    """A device checked in by serial only (no brand/model) used to 500 the whole
    board via `None + " " + None`. It must now render as a normal card."""
    loc = await _loc(client, tenant)
    cid = (await client.post("/api/clients", json={"name": "Bo", "phone": "+15550001"},
                             headers=tenant)).json()["id"]
    o = await client.post("/api/orders", json={
        "client_id": cid, "location_id": loc, "complaint": "screen",
        "asset_serial": "SN-ONLY-123"}, headers=tenant)
    assert o.status_code == 201, o.text

    b = await client.get("/api/orders/board?is_estimate=false", headers=tenant)
    assert b.status_code == 200, f"board 500'd on a null-field device: {b.text[:300]}"

    cards = [c for col in b.json()["columns"] for c in col["cards"]]
    assert len(cards) == 1
    # device with no brand/model comes back as None rather than "None None"
    assert cards[0]["device"] in (None, "")


@pytest.mark.asyncio
async def test_board_renders_partial_device_name(client, tenant):
    """Brand present but model missing (or vice-versa) should not crash and
    should trim cleanly."""
    loc = await _loc(client, tenant)
    cid = (await client.post("/api/clients", json={"name": "Ada", "phone": "+15550002"},
                             headers=tenant)).json()["id"]
    o = await client.post("/api/orders", json={
        "client_id": cid, "location_id": loc, "complaint": "battery",
        "asset_brand": "Apple"}, headers=tenant)   # no model
    assert o.status_code == 201, o.text

    b = await client.get("/api/orders/board?is_estimate=false", headers=tenant)
    assert b.status_code == 200, b.text
    cards = [c for col in b.json()["columns"] for c in col["cards"]]
    assert any((c["device"] or "").strip() == "Apple" for c in cards), \
        [c["device"] for c in cards]


@pytest.mark.asyncio
async def test_board_cards_carry_complaint_due_and_age(client, tenant):
    """Tekly Go's Today rows read "Device · Problem" and the promised time off board cards."""
    loc = await _loc(client, tenant)
    cid = (await client.post("/api/clients", json={"name": "Cy", "phone": "+15550003"},
                             headers=tenant)).json()["id"]
    o = await client.post("/api/orders", json={
        "client_id": cid, "location_id": loc, "complaint": "Cracked screen",
        "asset_brand": "Apple", "asset_model": "iPhone 14 Pro",
        "due_at": "2030-01-02T15:30:00"}, headers=tenant)
    assert o.status_code == 201, o.text

    b = await client.get("/api/orders/board?is_estimate=false", headers=tenant)
    assert b.status_code == 200, b.text
    card = [c for col in b.json()["columns"] for c in col["cards"]][0]
    assert card["device"] == "Apple iPhone 14 Pro"
    assert card["complaint"] == "Cracked screen"
    assert card["due_at"].startswith("2030-01-02T15:30")
    assert card["created_at"]
    assert card["age_days"] == 0
    assert card["overdue"] is False
