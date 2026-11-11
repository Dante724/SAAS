"""Hardening council M15: re-running a clients file that only has names (nothing to match on)
created every client again, and the first batch could no longer be undone ("only the most recent
import of this list can be undone"). A double click on Import, a browser retry, or "did that
work?" doubled the list with no way back.

The commit now keeps a content hash of (kind, headers, rows, mapping) on the batch's undo JSON and
refuses a byte-identical re-commit while the earlier batch is still inside the undo window, with a
409 that names it. Undoing the batch, or a different file, is accepted as before. Matching on name
as a last resort is a behaviour change for the owner to decide and is not part of this.
"""
import io
import re

import pytest

pytestmark = pytest.mark.asyncio

MAPPING = {"Name": "name", "Address": "address"}


async def _parse(client, h, text, name="clients.csv"):
    r = await client.post("/api/imports/parse?kind=clients", headers=h,
                          files={"file": (name, io.BytesIO(text.encode()), "text/csv")})
    assert r.status_code == 200, r.text
    return r.json()


async def _commit(client, h, p, mapping=MAPPING, *, kind="clients"):
    return await client.post(f"/api/imports/{kind}/commit", headers=h, json={
        "headers": p["headers"], "rows": p["rows"], "row_numbers": p["row_numbers"],
        "mapping": mapping, "file_name": "clients.csv"})


async def _preview(client, h, p, mapping=MAPPING):
    r = await client.post("/api/imports/clients/preview", headers=h, json={
        "headers": p["headers"], "rows": p["rows"], "row_numbers": p["row_numbers"], "mapping": mapping})
    assert r.status_code == 200, r.text
    return r.json()


async def _n_clients(client, h):
    return len((await client.get("/api/clients?limit=100", headers=h)).json()["items"])


async def test_identical_rerun_is_refused_and_the_first_batch_stays_undoable(client, tenant):
    p = await _parse(client, tenant, "Name,Address\nAlice Jones,1 High St\nBob Smith,2 High St\n")
    first = await _commit(client, tenant, p)
    assert first.status_code == 200 and first.json()["created"] == 2, first.text

    second = await _commit(client, tenant, p)
    assert second.status_code == 409, second.text
    msg = second.json()["detail"]
    assert "already imported" in msg and first.json()["batch_id"] in msg, msg
    assert re.search(r"\d+ minutes? ago", msg), msg
    assert await _n_clients(client, tenant) == 2

    undo = await client.post(f"/api/imports/batches/{first.json()['batch_id']}/undo", headers=tenant)
    assert undo.status_code == 200, undo.text
    assert await _n_clients(client, tenant) == 0


async def test_preview_warns_about_an_identical_earlier_import(client, tenant):
    p = await _parse(client, tenant, "Name,Address\nAlice Jones,1 High St\n")
    assert (await _commit(client, tenant, p)).status_code == 200
    pre = await _preview(client, tenant, p)
    assert any("already imported" in w for w in pre["warnings"]), pre["warnings"]


async def test_rerun_is_allowed_after_the_batch_was_undone(client, tenant):
    p = await _parse(client, tenant, "Name,Address\nAlice Jones,1 High St\n")
    first = await _commit(client, tenant, p)
    assert first.status_code == 200
    assert (await client.post(f"/api/imports/batches/{first.json()['batch_id']}/undo", headers=tenant)).status_code == 200
    again = await _commit(client, tenant, p)
    assert again.status_code == 200 and again.json()["created"] == 1, again.text


async def test_a_changed_file_or_mapping_is_not_a_rerun(client, tenant):
    p = await _parse(client, tenant, "Name,Address\nAlice Jones,1 High St\n")
    assert (await _commit(client, tenant, p)).status_code == 200
    # One more row: a new file, even though Alice is in it again (she's created again: the name-only
    # match is the follow-up the owner decides on).
    p2 = await _parse(client, tenant, "Name,Address\nAlice Jones,1 High St\nCarol King,3 High St\n")
    r = await _commit(client, tenant, p2)
    assert r.status_code == 200, r.text
    # Same rows, mapping that ignores the address: a different import.
    r = await _commit(client, tenant, p, {"Name": "name"})
    assert r.status_code == 200, r.text


async def test_rerun_of_a_file_that_matches_on_phone_is_also_refused(client, tenant):
    """The guard is about the same bytes twice, whatever the list: a phone file re-run would only
    update, but nothing in it has changed, so the second click is still answered."""
    p = await _parse(client, tenant, "Name,Phone\nAlice Jones,0412000111\n")
    mapping = {"Name": "name", "Phone": "phone"}
    assert (await _commit(client, tenant, p, mapping)).status_code == 200
    r = await _commit(client, tenant, p, mapping)
    assert r.status_code == 409, r.text


async def test_rerun_is_allowed_once_the_undo_window_has_passed(client, tenant, db):
    from datetime import datetime, timedelta

    from sqlalchemy import update

    from backend.models import ImportBatch
    p = await _parse(client, tenant, "Name,Address\nAlice Jones,1 High St\n")
    first = await _commit(client, tenant, p)
    assert first.status_code == 200
    await db.execute(update(ImportBatch).where(ImportBatch.id == first.json()["batch_id"])
                     .values(created_at=datetime.utcnow() - timedelta(hours=25)))
    await db.commit()
    again = await _commit(client, tenant, p)
    assert again.status_code == 200 and again.json()["created"] == 1, again.text
