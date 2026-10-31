"""Customer portal on React: /portal/{token} serves the React shell (main.tsx renders
<PublicPortal/>), and a portal job returns the same payload as the track page so the two pages
show one job identically."""
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def react_shell(tmp_path, monkeypatch):
    # CI runs without a frontend build; point the app at a stand-in shell (see test_spa_path_traversal).
    import main
    shell = tmp_path / "index.html"
    shell.write_text('<!doctype html><html><body><div id="root" data-shell="react"></div></body></html>')
    monkeypatch.setattr(main, "_have_react", True)
    monkeypatch.setattr(main, "_react_index", str(shell))
    return shell


@pytest.mark.asyncio
async def test_portal_path_serves_the_react_shell(client, react_shell):
    r = await client.get("/portal/some-token")
    assert r.status_code == 200
    assert 'data-shell="react"' in r.text
    assert "design.css" not in r.text          # not the legacy vanilla-JS page


def test_legacy_portal_page_is_gone():
    assert not os.path.exists(os.path.join(ROOT, "frontend", "portal.html"))


async def _job(client, h, is_estimate=False):
    loc = (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]
    cid = (await client.post("/api/clients", json={"name": "Ana Lima", "phone": "+15550199"}, headers=h)).json()["id"]
    o = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "cracked",
                                                "is_estimate": is_estimate}, headers=h)).json()
    await client.post(f"/api/orders/{o['id']}/lines",
                      json={"line_type": "labour", "description": "Screen", "qty": 1, "unit_price": 80}, headers=h)
    ptoken = (await client.post(f"/api/clients/{cid}/portal-link", headers=h)).json()["portal_token"]
    track = (await client.get(f"/api/orders/{o['id']}", headers=h)).json()["public_token"]
    return o, ptoken, track


@pytest.mark.asyncio
async def test_portal_job_matches_the_track_page(client, tenant):
    o, ptoken, track = await _job(client, tenant)
    job = (await client.get(f"/api/public/portal/{ptoken}/order/{o['id']}")).json()
    page = (await client.get(f"/api/public/track/{track}")).json()
    for k, v in page.items():
        assert job[k] == v, k                    # every track field, identical
    assert job["id"] == o["id"] and job["public_token"] == track
    assert job["lines"][0]["label"] == "Screen" and job["lines"][0]["amount"] == 80


@pytest.mark.asyncio
async def test_portal_home_says_what_needs_the_customer(client, tenant):
    await client.get("/api/statuses?is_estimate=true", headers=tenant)   # seed estimate statuses
    est, ptoken, track = await _job(client, tenant, is_estimate=True)
    home = (await client.get(f"/api/public/portal/{ptoken}")).json()
    card = next(c for c in home["estimates"] if c["id"] == est["id"])
    # the list names the status exactly as the job page does (the shop's own names)
    assert card["stage_label"] == (await client.get(f"/api/public/track/{track}")).json()["stage_label"]
    assert card["awaiting_approval"] is True
    assert card["open_recommendations"] == 0
    assert card["currency_symbol"] and card["total_converted"] == card["total"]
    assert "logo_data" in home and "brand_color" in home


@pytest.mark.asyncio
async def test_portal_job_of_another_customer_is_404(client, tenant):
    o, _, _ = await _job(client, tenant)
    _, other, _ = await _job(client, tenant)
    assert (await client.get(f"/api/public/portal/{other}/order/{o['id']}")).status_code == 404
