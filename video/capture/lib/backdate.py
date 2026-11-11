"""Move seeded jobs back in time inside the capture's own SQLite DB (demo history for reports).

usage: python -I backdate.py <db> <json {order_id: days_ago}>

Every date/time column on the job, its lines, invoices, payments, cash movements, status log and
feed items is shifted back by the same number of days, so the job reads as made, finished and
paid that day. Only touches the throwaway capture database.
"""
import json
import sqlite3
import sys
from datetime import datetime, timedelta

db, spec = sys.argv[1], json.load(open(sys.argv[2]))
con = sqlite3.connect(db, timeout=30)
cur = con.cursor()
tables = [r[0] for r in cur.execute("select name from sqlite_master where type='table'")]
cols = {t: [(c[1], (c[2] or "").upper()) for c in cur.execute(f'pragma table_info("{t}")')] for t in tables}


def shift(v, days):
    if not isinstance(v, str) or len(v) < 10:
        return v
    if len(v) == 10:
        try:
            return (datetime.strptime(v, "%Y-%m-%d") - timedelta(days=days)).strftime("%Y-%m-%d")
        except ValueError:
            return v
    head, sep, tail = v.partition(".")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            d = datetime.strptime(head[:19], fmt)
        except ValueError:
            continue
        return (d - timedelta(days=days)).strftime(fmt) + head[19:] + sep + tail
    return v


def date_cols(t):
    return [c for c, ty in cols[t] if "DATE" in ty or "TIME" in ty]


total = 0
for oid, val in spec.items():
    days, open_h = (val, 0) if isinstance(val, (int, float)) else (val[0], val[1])
    # the job's invoices and payments, to follow them into their own tables
    inv_ids, pay_ids = set(), set()
    for t in tables:
        names = [c for c, _ in cols[t]]
        if "order_id" in names and t.startswith("invoice"):
            col = "invoice_id" if "invoice_id" in names else "id"
            inv_ids |= {r[0] for r in cur.execute(f'select {col} from "{t}" where order_id=?', (oid,))}
    for t in tables:
        names = [c for c, _ in cols[t]]
        if t.startswith("payment") and "id" in names:
            q = " or ".join(x for x in ["order_id=?" if "order_id" in names else "",
                                         f"invoice_id in ({','.join('?' * len(inv_ids))})" if "invoice_id" in names and inv_ids else ""] if x)
            if q:
                args = ([oid] if "order_id" in names else []) + (list(inv_ids) if "invoice_id" in names and inv_ids else [])
                pay_ids |= {r[0] for r in cur.execute(f'select id from "{t}" where {q}', args)}
    keys = {"order_id": {oid}, "invoice_id": inv_ids, "payment_id": pay_ids}
    for t in tables:
        dcs = date_cols(t)
        if not dcs:
            continue
        names = [c for c, _ in cols[t]]
        conds, args = [], []
        if t == "orders":
            conds.append("id=?"); args.append(oid)
        if t.startswith("invoice") and "id" in names and inv_ids and t in ("invoices",):
            conds.append(f"id in ({','.join('?' * len(inv_ids))})"); args += list(inv_ids)
        if t.startswith("payment") and t in ("payments",) and pay_ids:
            conds.append(f"id in ({','.join('?' * len(pay_ids))})"); args += list(pay_ids)
        for k, ids in keys.items():
            if k in names and ids:
                conds.append(f"{k} in ({','.join('?' * len(ids))})"); args += list(ids)
        if "entity_id" in names:
            conds.append("entity_id=?"); args.append(oid)
        if not conds:
            continue
        rows = cur.execute(f'select rowid, {",".join(dcs)} from "{t}" where {" or ".join(conds)}', args).fetchall()
        for r in rows:
            vals = [shift(v, days) for v in r[1:]]
            cur.execute(f'update "{t}" set {",".join(f"{c}=?" for c in dcs)} where rowid=?', vals + [r[0]])
            total += 1
    if open_h:   # the job came in some hours before it was finished
        r = cur.execute("select created_at from orders where id=?", (oid,)).fetchone()
        if r and r[0]:
            head, sep, tail = r[0].partition(".")
            d = datetime.strptime(head[:19].replace("T", " "), "%Y-%m-%d %H:%M:%S") - timedelta(hours=open_h)
            cur.execute("update orders set created_at=? where id=?", (d.strftime("%Y-%m-%d %H:%M:%S") + sep + tail, oid))
con.commit()
print(f"backdated {len(spec)} jobs ({total} rows)")
