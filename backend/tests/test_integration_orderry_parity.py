"""Integration fixes found while merging the Orderry-parity workstreams."""


async def test_template_edit_keeps_auto_print_settings(client, tenant):
    base = {"name": "Ticket", "doc_type": "work_order", "body_html": "<p>x</p>"}
    tid = (await client.post("/api/documents/templates", headers=tenant, json={
        **base, "auto_print_on": "create", "print_statuses": ["new"]})).json()["id"]
    # The template editor saves without the auto-print fields: they must survive.
    assert (await client.put(f"/api/documents/templates/{tid}", json={**base, "name": "Ticket 2"},
                             headers=tenant)).status_code == 200
    rows = (await client.get("/api/documents/templates", headers=tenant)).json()
    t = next(r for r in (rows["items"] if isinstance(rows, dict) else rows) if r["id"] == tid)
    assert t["name"] == "Ticket 2" and t["auto_print_on"] == "create" and t["print_statuses"] == ["new"]
    # Sending them explicitly still clears them.
    await client.put(f"/api/documents/templates/{tid}", headers=tenant,
                     json={**base, "auto_print_on": None, "print_statuses": []})
    rows = (await client.get("/api/documents/templates", headers=tenant)).json()
    t = next(r for r in (rows["items"] if isinstance(rows, dict) else rows) if r["id"] == tid)
    assert t["auto_print_on"] is None and t["print_statuses"] == []


async def test_pos_receipt_uses_org_currency(client, tenant):
    from backend.services.currency import _base

    class Org:
        currency = None
        currency_code = "USD"
    assert _base(Org())["symbol"] == "$"


def test_wall_clock_conversion():
    from datetime import datetime, timezone

    from backend.services.dates import to_wall_clock
    naive = datetime(2026, 10, 21, 9, 0)
    assert to_wall_clock(naive, "America/New_York") == naive                    # already shop time
    utc = datetime(2026, 10, 21, 13, 0, tzinfo=timezone.utc)                     # 9:00 in New York
    assert to_wall_clock(utc, "America/New_York") == datetime(2026, 10, 21, 9, 0)
    assert to_wall_clock(utc, "Not/AZone") == datetime(2026, 10, 21, 13, 0)      # unknown zone → UTC


async def test_reschedule_with_a_zoned_instant_stores_shop_time(client, tenant, db):
    from sqlalchemy import update

    from backend.models import Location, Organisation
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    await db.execute(update(Organisation).where(Organisation.id == me["org_id"]).values(timezone="America/New_York"))
    await db.execute(update(Location).where(Location.org_id == me["org_id"]).values(timezone="America/New_York"))
    await db.commit()
    cid = (await client.post("/api/clients", json={"name": "TZ", "phone": "+15550001111"}, headers=tenant)).json()["id"]
    oid = (await client.post("/api/orders", json={"client_id": cid, "complaint": "x"}, headers=tenant)).json()["id"]
    # A browser in any zone sends an instant; 13:00Z is 09:00 at the shop.
    r = await client.patch(f"/api/orders/{oid}/schedule", headers=tenant,
                           json={"scheduled_for": "2026-10-21T13:00:00Z", "scheduled_to": "2026-10-21T15:00:00+00:00"})
    assert r.status_code == 200, r.text
    o = (await client.get(f"/api/orders/{oid}", headers=tenant)).json()
    assert o["scheduled_for"].startswith("2026-10-21T09:00") and o["scheduled_to"].startswith("2026-10-21T11:00")
    # A wall-clock value (what the staff screens send) is stored as-is.
    await client.patch(f"/api/orders/{oid}/schedule", headers=tenant, json={"scheduled_for": "2026-10-22T10:30"})
    o = (await client.get(f"/api/orders/{oid}", headers=tenant)).json()
    assert o["scheduled_for"].startswith("2026-10-22T10:30")
