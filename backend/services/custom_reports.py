"""144jq — custom report builder.

A *safe*, whitelist-driven query builder. The client sends a JSON spec
(source, columns, filters, group_by, aggregate); this module validates every
piece against a fixed whitelist and builds a parameterised SQLAlchemy query.

Multi-tenant safety, non-negotiable:
- Only sources/fields/ops in the whitelist below are ever touched — a spec that
  names anything else raises ValueError (never reaches the DB).
- `org_id == <caller's org>` is ALWAYS applied. There is no way to widen it.
- Values go through SQLAlchemy parameters, never string-interpolated SQL.
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Order, Invoice, Payment, Client, Part
from backend.services.money import q2


# ── Whitelist ────────────────────────────────────────────────────────────────
# Each field: type drives which operators/aggregates are allowed and how the
# value renders. `date_field` is what the report's date range filters on.
def _f(label, type_):
    return {"label": label, "type": type_}


SOURCES: dict[str, dict] = {
    "orders": {
        "label": "Work orders", "model": Order, "date_field": "created_at", "has_location": True,
        "fields": {
            "order_number": _f("Order #", "text"), "status": _f("Status", "text"),
            "is_estimate": _f("Is estimate", "bool"),
            "created_at": _f("Created", "date"), "closed_at": _f("Closed", "date"),
        },
    },
    "invoices": {
        "label": "Invoices", "model": Invoice, "date_field": "issued_at", "has_location": False,
        "fields": {
            "invoice_number": _f("Invoice #", "text"), "status": _f("Status", "text"),
            "subtotal": _f("Subtotal", "money"), "discount": _f("Discount", "money"),
            "tax": _f("Tax", "money"), "total": _f("Total", "money"),
            "amount_paid": _f("Paid", "money"),
            "created_at": _f("Created", "date"), "issued_at": _f("Issued", "date"),
        },
    },
    "payments": {
        "label": "Payments", "model": Payment, "date_field": "paid_at", "has_location": True,
        "fields": {
            "method": _f("Method", "text"), "kind": _f("Kind", "text"),
            "amount": _f("Amount", "money"), "reference": _f("Reference", "text"),
            "paid_at": _f("Paid at", "date"),
        },
    },
    "clients": {
        "label": "Clients", "model": Client, "date_field": "created_at", "has_location": False,
        "fields": {
            "name": _f("Name", "text"), "client_type": _f("Type", "text"),
            "phone": _f("Phone", "text"), "email": _f("Email", "text"),
            "loyalty_tier": _f("Loyalty tier", "text"), "created_at": _f("Created", "date"),
        },
    },
    "products": {
        "label": "Products", "model": Part, "date_field": None, "has_location": False,
        "fields": {
            "sku": _f("SKU", "text"), "name": _f("Name", "text"), "brand": _f("Brand", "text"),
            "category": _f("Category", "text"), "cost_price": _f("Cost", "money"),
            "sell_price": _f("Sell price", "money"), "repair_price": _f("Repair price", "money"),
            "min_stock": _f("Min stock", "number"),
        },
    },
}

_NUMERIC = ("money", "number")
_TEXT_OPS = ("eq", "ne", "contains")
_NUM_OPS = ("eq", "ne", "gt", "lt", "gte", "lte")
_DATE_OPS = ("gt", "lt", "gte", "lte")
AGG_FNS = ("count", "sum", "avg")


def sources_meta() -> dict:
    """Whitelist description for the builder UI (no models)."""
    return {
        key: {
            "label": s["label"], "date_field": s["date_field"], "has_location": s["has_location"],
            "fields": [{"key": k, "label": v["label"], "type": v["type"]} for k, v in s["fields"].items()],
        }
        for key, s in SOURCES.items()
    }


def _ops_for(ftype: str):
    if ftype in _NUMERIC:
        return _NUM_OPS
    if ftype == "date":
        return _DATE_OPS
    return _TEXT_OPS


def _coerce(ftype: str, value):
    if ftype in _NUMERIC:
        return float(value)
    if ftype == "bool":
        return str(value).lower() in ("1", "true", "yes", "y")
    if ftype == "date":
        # parse so a bad date is caught here (→ friendly message) rather than leaking an
        # unparsed string into the SQL comparison (code-review C).
        return datetime.fromisoformat(str(value))
    return str(value)


def _clause(col, op: str, value):
    return {
        "eq": col == value, "ne": col != value,
        "gt": col > value, "lt": col < value, "gte": col >= value, "lte": col <= value,
        "contains": col.ilike(f"%{value}%"),
    }[op]


def _render(ftype: str, v):
    if v is None:
        return ""
    if ftype == "date":
        return v.strftime("%Y-%m-%d") if hasattr(v, "strftime") else str(v)
    if ftype in _NUMERIC:
        return float(v)
    if ftype == "bool":
        return "Yes" if v else "No"
    return str(v)


def _col(src, key):
    if key not in src["fields"]:
        raise ValueError(f"Unknown field '{key}' for source")
    return getattr(src["model"], key)


async def build_and_run(db: AsyncSession, org_id: str, config: dict,
                        date_from: Optional[str] = None, date_to: Optional[str] = None,
                        location_id: Optional[str] = None) -> dict:
    source = (config or {}).get("source")
    if source not in SOURCES:
        raise ValueError(f"Unknown source '{source}'")
    src = SOURCES[source]
    model, fields = src["model"], src["fields"]

    # ---- WHERE: org (always) + date range + location + user filters ----
    conds = [model.org_id == org_id]
    if src["date_field"] and (date_from or date_to):
        dcol = getattr(model, src["date_field"])
        try:
            if date_from:
                conds.append(dcol >= datetime.fromisoformat(date_from))
            if date_to:
                dt = datetime.fromisoformat(date_to)
                if dt.time() == datetime.min.time():
                    from datetime import timedelta
                    dt = dt + timedelta(days=1) - timedelta(seconds=1)
                conds.append(dcol <= dt)
        except ValueError:
            raise ValueError("Invalid date in the date range (expected YYYY-MM-DD).")
    if src["has_location"] and location_id:
        conds.append(model.location_id == location_id)
    for flt in (config.get("filters") or []):
        fk, op = flt.get("field"), flt.get("op")
        if fk not in fields:
            raise ValueError(f"Unknown filter field '{fk}'")
        ftype = fields[fk]["type"]
        if op not in _ops_for(ftype):
            raise ValueError(f"Operator '{op}' not allowed on {ftype} field '{fk}'")
        try:
            val = _coerce(ftype, flt.get("value"))
        except (TypeError, ValueError):
            raise ValueError(f"'{flt.get('value')}' is not a valid value for {fields[fk]['label']}.")
        conds.append(_clause(getattr(model, fk), op, val))

    group_by = config.get("group_by")

    # ---- GROUP BY + aggregates ----
    if group_by:
        if group_by not in fields:
            raise ValueError(f"Unknown group_by '{group_by}'")
        gcol = getattr(model, group_by)
        out_cols = [{"key": group_by, "label": fields[group_by]["label"], "align": "left"}]
        select_exprs = [gcol]
        aggs = config.get("aggregates") or [{"fn": "count"}]
        for a in aggs:
            fn = a.get("fn")
            if fn not in AGG_FNS:
                raise ValueError(f"Unknown aggregate '{fn}'")
            if fn == "count":
                select_exprs.append(func.count())
                out_cols.append({"key": "count", "label": "Count", "align": "right"})
            else:
                afk = a.get("field")
                if afk not in fields or fields[afk]["type"] not in _NUMERIC:
                    raise ValueError(f"{fn} needs a numeric field, got '{afk}'")
                select_exprs.append((func.sum if fn == "sum" else func.avg)(getattr(model, afk)))
                out_cols.append({"key": f"{fn}_{afk}", "label": f"{fn.title()} {fields[afk]['label']}", "align": "right"})
        q = select(*select_exprs).where(*conds).group_by(gcol).order_by(gcol)
        res = (await db.execute(q)).all()
        rows = []
        for row in res:
            out = [_render(fields[group_by]["type"], row[0])]
            for i in range(1, len(select_exprs)):
                v = row[i]
                out.append(q2(float(v)) if v is not None else 0)
            rows.append(out)
        return {"columns": out_cols, "rows": rows, "row_count": len(rows)}

    # ---- Flat mode ----
    keys = config.get("columns") or list(fields.keys())
    keys = [k for k in keys if k in fields] or list(fields.keys())
    out_cols = [{"key": k, "label": fields[k]["label"], "align": "right" if fields[k]["type"] in _NUMERIC else "left"} for k in keys]
    q = select(*[getattr(model, k) for k in keys]).where(*conds)
    sort = config.get("sort")
    if sort and sort.get("field") in fields:
        scol = getattr(model, sort["field"])
        q = q.order_by(scol.desc() if sort.get("dir") == "desc" else scol.asc())
    limit = min(int(config.get("limit") or 500), 2000)
    q = q.limit(limit)
    res = (await db.execute(q)).all()
    rows = [[_render(fields[k]["type"], row[i]) for i, k in enumerate(keys)] for row in res]
    return {"columns": out_cols, "rows": rows, "row_count": len(rows)}
