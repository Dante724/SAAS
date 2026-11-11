"""Today on the test shop's clock, the day reports and the cash panel count by.

Tests must not build "today" from date.today() or now_utc().date(): a new shop defaults to
Asia/Kolkata (UTC+5:30), so from 18:30 UTC the shop's day is already tomorrow and a UTC "today"
range misses everything that just happened.
"""
from backend.db.database import get_db
from backend.services.dates import shop_today


async def shop_today_for(client, headers):
    from main import app
    org_id = (await client.get("/api/auth/me", headers=headers)).json()["org_id"]
    gen = app.dependency_overrides.get(get_db, get_db)()
    db = await gen.__anext__()
    try:
        return await shop_today(db, org_id)
    finally:
        await gen.aclose()
