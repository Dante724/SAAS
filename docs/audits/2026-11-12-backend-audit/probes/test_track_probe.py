"""Audit probe: public tracking page payload size and query count with client-visible photos."""
import json, os
import pytest
from sqlalchemy import event, select

PNG = "data:image/png;base64," + ("iVBORw0KGgo" + "A" * 200_000)


@pytest.mark.asyncio
async def test_track(client, tenant, db):
    cid = (await client.post("/api/clients", json={"name": "T"}, headers=tenant)).json()["id"]
    oid = (await client.post("/api/orders", json={"client_id": cid}, headers=tenant)).json()["id"]
    out = {}
    from backend.models import Order
    tok = (await db.execute(select(Order.public_token).where(Order.id == oid))).scalar_one()
    eng = db.bind.sync_engine
    for n in (0, 2, 4):
        while len(out) < n or (n and out.get("_added", 0) < n):
            r = await client.post(f"/api/feed/order/{oid}", headers=tenant,
                                  json={"item_type": "photo", "media_data": PNG, "visibility": "client"})
            if r.status_code != 201:
                out.setdefault("upload_errors", []).append(r.status_code); break
            out["_added"] = out.get("_added", 0) + 1
        cnt = [0]
        def hit(*a): cnt[0] += 1
        event.listen(eng, "before_cursor_execute", hit)
        r = await client.get(f"/api/public/track/{tok}")
        event.remove(eng, "before_cursor_execute", hit)
        out[f"photos={n}"] = {"status": r.status_code, "bytes": len(r.content), "queries": cnt[0],
                              "etag": r.headers.get("etag"), "cache": r.headers.get("cache-control")}
    json.dump(out, open(os.environ["PROBE_OUT4"], "w"), indent=1)
