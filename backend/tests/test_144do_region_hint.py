"""144do — signup-options derives a region hint from the CDN country header
(no IP stored); unknown/absent country yields no hint. A suggestion only."""
import pytest

from backend.routers.auth import _region_hint


def test_region_hint_mapping():
    assert _region_hint("US") == "us"
    assert _region_hint("de") == "eu"       # case-insensitive, EU bucket
    assert _region_hint("AE") == "mena"
    assert _region_hint("CA") == "ca"
    assert _region_hint("IN") == "other"
    assert _region_hint("") is None
    assert _region_hint(None) is None


@pytest.mark.asyncio
async def test_signup_options_reads_country_header(client):
    r = await client.get("/api/auth/signup-options", headers={"CF-IPCountry": "FR"})
    assert r.status_code == 200, r.text
    assert r.json()["region_hint"] == "eu"
    # no header → no hint (never guesses blindly)
    r2 = await client.get("/api/auth/signup-options")
    assert r2.json().get("region_hint") is None
