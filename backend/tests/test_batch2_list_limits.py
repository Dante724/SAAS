"""Batch 2 — staff lists serve at most 200 rows per request (a bigger limit is clamped, an
absurd one rejected), page with skip, and report the filtered total."""
import pytest

LISTS = ["/api/finance/invoices", "/api/assets", "/api/catalog/services",
         "/api/timeclock/entries", "/api/cash/transactions", "/api/clients"]


@pytest.mark.asyncio
@pytest.mark.parametrize("path", LISTS)
async def test_list_limit_is_clamped_and_bounded(client, tenant, path):
    r = await client.get(f"{path}?limit=1000", headers=tenant)
    assert r.status_code == 200, r.text
    assert r.json()["limit"] == 200
    assert (await client.get(f"{path}?limit=100000", headers=tenant)).status_code == 422
    assert (await client.get(f"{path}?skip=-1", headers=tenant)).status_code == 422


@pytest.mark.asyncio
async def test_clients_total_follows_search_and_pages(client, tenant):
    for i in range(5):
        await client.post("/api/clients", json={"name": f"Zed {i}", "phone": f"+1555900{i}"}, headers=tenant)
    await client.post("/api/clients", json={"name": "Other"}, headers=tenant)
    r = (await client.get("/api/clients?search=zed&limit=2", headers=tenant)).json()
    assert r["total"] == 5 and len(r["items"]) == 2
    ids = {c["id"] for s in (0, 2, 4) for c in
           (await client.get(f"/api/clients?search=zed&limit=2&skip={s}", headers=tenant)).json()["items"]}
    assert len(ids) == 5


@pytest.mark.asyncio
async def test_assets_page_with_total_counts_and_no_inline_photo(client, tenant):
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    cid = (await client.post("/api/clients", json={"name": "Owner"}, headers=tenant)).json()["id"]
    for i in range(3):
        o = await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x",
                                                   "asset_brand": "Apple", "asset_model": f"M{i}"}, headers=tenant)
        assert o.status_code == 201, o.text
    dev = o.json()["device"]["id"]
    await client.put(f"/api/assets/{dev}", json={
        "image_data": "data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw=="}, headers=tenant)
    r = (await client.get("/api/assets?limit=2", headers=tenant)).json()
    assert r["total"] == 3 and len(r["items"]) == 2
    row = next(a for a in (await client.get("/api/assets", headers=tenant)).json()["items"] if a["id"] == dev)
    assert "image_data" not in row and row["image_url"] == f"/api/assets/{dev}/image"
    assert row["owner_name"] == "Owner" and row["order_count"] == 1
    # the detail still carries the photo itself
    assert (await client.get(f"/api/assets/{dev}", headers=tenant)).json()["image_data"].startswith("data:")


@pytest.mark.asyncio
async def test_invoice_search_and_services_total_are_filtered(client, tenant):
    for n in ("Alpha fit", "Alpha swap", "Beta clean"):
        await client.post("/api/catalog/services", json={"name": n, "price": 10}, headers=tenant)
    r = (await client.get("/api/catalog/services?search=alpha&limit=1", headers=tenant)).json()
    assert r["total"] == 2 and len(r["items"]) == 1
    inv = (await client.get("/api/finance/invoices?search=nothing-matches", headers=tenant)).json()
    assert inv["total"] == 0 and inv["items"] == []


@pytest.mark.asyncio
async def test_cash_summary_covers_every_row_not_just_the_page(client, tenant):
    from datetime import date
    await client.post("/api/cash/accounts", json={"name": "Till", "payment_option": "cash"}, headers=tenant)
    accts = (await client.get("/api/cash/accounts", headers=tenant)).json()
    accts = accts.get("items", accts) if isinstance(accts, dict) else accts
    aid = accts[0]["id"]
    for amt, kind in ((10, "income"), (20, "income"), (5, "expense")):
        r = await client.post("/api/cash/transactions", json={"account_id": aid, "txn_type": kind, "amount": amt},
                              headers=tenant)
        assert r.status_code in (200, 201), r.text
    r = (await client.get(f"/api/cash/transactions?account_id={aid}&limit=1&summary=true&day={date.today()}",
                          headers=tenant)).json()
    assert len(r["items"]) == 1 and r["total"] >= 3
    s = r["summary"]
    assert s["count"] >= 3 and s["income"] >= 30 and s["expense"] >= 5
    assert s["today_in"] >= 30 and s["today_out"] >= 5
