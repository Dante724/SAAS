"""Batch lookups for list endpoints, so a page costs a fixed number of queries.

The pattern these replace ran one query per row ("for r in rows: select lines where
parent == r.id"), so a 200-row page cost 200+ queries. Both helpers take the ids the page
needs and fetch them in one `IN (...)` query, chunked so a large id list stays under the
SQLite/Postgres bind-parameter limits.
"""
from collections import defaultdict
from collections.abc import Iterable

from sqlalchemy import select

_CHUNK = 500


def _ids(values: Iterable) -> list:
    return list({v for v in values if v is not None})


async def names_by_id(db, id_col, name_col, ids: Iterable, *, org_col=None, org_id=None) -> dict:
    """{id: name} for just these ids, selecting only the two columns (never the whole table).
    Pass org_col/org_id to keep the lookup inside one shop."""
    want = _ids(ids)
    out: dict = {}
    for i in range(0, len(want), _CHUNK):
        q = select(id_col, name_col).where(id_col.in_(want[i:i + _CHUNK]))
        if org_col is not None:
            q = q.where(org_col == org_id)
        out.update((k, v) for k, v in (await db.execute(q)).all())
    return out


async def rows_by(db, model, fk_col, ids: Iterable, *order_by) -> dict:
    """{parent_id: [child rows]} for every parent in `ids`, in one query per chunk.
    Parents with no children are simply missing — use `.get(pid, [])`."""
    want = _ids(ids)
    out: dict = defaultdict(list)
    for i in range(0, len(want), _CHUNK):
        q = select(model).where(fk_col.in_(want[i:i + _CHUNK]))
        if order_by:
            q = q.order_by(*order_by)
        for row in (await db.execute(q)).scalars().all():
            out[getattr(row, fk_col.key)].append(row)
    return out
