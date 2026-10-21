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
