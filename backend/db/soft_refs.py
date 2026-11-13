"""Soft id columns: plain String `*_id` columns with no foreign key (audit SEC-08).

About 80 such columns exist. Most hold an outside id (Stripe, QuickBooks, a provider message id) or
are written only by the app itself from rows it has just loaded. The ones below take an id from a
staff request, and nothing checked them: shop A could save shop B's price list on a location (probe
P8), or an id that is nobody's. The database can't refuse it (no FK) and the flush-time tenant guard
(tenancy.py) only knew about a few line columns, and even those accept an id that matches no row.

One check covers every writer: before each flush in a shop's session, a registered column that is
being set (a new row, or a changed value) must name a row of the right kind in that same shop, else
422. Another shop's id reads exactly like an unknown one. A value already stored is never re-checked,
so a row holding a stale id from before this check can still be edited in other ways.

To cover a new soft column, add it to `_registry()`. Better still, make it a real foreign key (and
keep it registered here if staff can set it: this check gives the clear 422 before the database's
FK or the tenant guard would refuse it).
"""
from functools import cache

from sqlalchemy import event, inspect, select
from sqlalchemy.orm import Session


def _by(attr: str, targets: dict):
    """A column whose target table depends on a sibling "type" column (entity_type, upsell_kind...).
    An unlisted type (e.g. a "custom" upsell) names no row, so there is nothing to check."""
    def pick(obj):
        return targets.get((getattr(obj, attr, None) or "").strip().lower())
    return pick


@cache
def _registry() -> dict:
    """{table: {column: (target model(s) or picker(obj) -> model(s)|None, label)}}"""
    from backend import models as m
    catalog = {"service": m.Service, "labor": m.Service, "labour": m.Service, "product": m.Part}
    upsell = (_by("upsell_kind", catalog), "catalog item")
    price_list = (m.PriceType, "price list")
    pay_category = (m.CashFlowItem, "payment category")
    return {
        "locations": {"price_type_id": price_list},
        "organisations": {"price_type_tickets_id": price_list, "price_type_sales_id": price_list,
                          "price_type_invoices_id": price_list,
                          "cash_category_tickets_id": pay_category,
                          "cash_category_sales_id": pay_category},
        "custom_fields": {"upsell_ref_id": upsell},
        "checklist_template_items": {"upsell_ref_id": upsell},
        "order_checklist_items": {"upsell_ref_id": upsell},
        "price_type_items": {"item_id": (_by("item_type", catalog), "catalog item")},
        "bundle_components": {"ref_id": (_by("ref_type", catalog), "catalog item")},
        "tax_exceptions": {"target_id": (_by("target_type", {**catalog, "category": m.Category}),
                                         "item")},
        "taggables": {"entity_id": (_by("entity_type", {
            "contact": m.Client, "order": m.Order, "chat": (m.ChatThread, m.InboxThread)}), "record")},
        "directory_items": {"parent_id": (m.DirectoryItem, "list value")},
        "form_layouts": {"type_id": (m.WorkOrderType, "work-order type")},
        "invoice_lines": {"source_id": (_by("kind", catalog), "catalog item")},
        "message_handoffs": {"template_id": (m.MessageTemplate, "message template")},
    }


def _owner_org(obj, table: str, tenant):
    if table == "organisations":
        return obj.id               # the shop's own settings row
    return getattr(obj, "org_id", None) or tenant


def _wanted(session: Session, tenant) -> dict:
    """{(target models tuple, label): {id: {(org, "table.column")}}} for soft ids being written."""
    reg = _registry()
    out: dict = {}
    for obj in list(session.new) + list(session.dirty):
        table = getattr(getattr(obj, "__table__", None), "name", None)
        cols = reg.get(table)
        if not cols:
            continue
        state = inspect(obj)
        is_new = obj in session.new
        for col, (target, label) in cols.items():
            val = getattr(obj, col, None)
            if not val or not (is_new or state.attrs[col].history.added):
                continue
            models = target(obj) if callable(target) and not hasattr(target, "__table__") else target
            if not models:
                continue
            models = models if isinstance(models, tuple) else (models,)
            org = _owner_org(obj, table, tenant)
            out.setdefault((models, label), {}).setdefault(val, set()).add((org, f"{table}.{col}"))
    return out


# insert=True: runs before the tenant reference guard (tenancy.py). Two registered columns became
# real foreign keys in dbi01 (locations.price_type_id, directory_items.parent_id, audit DB-13); the
# tenant guard would otherwise refuse another shop's id first with its generic 404, and the staff
# member would lose this check's "that price list doesn't exist any more" message.
@event.listens_for(Session, "before_flush", insert=True)
def _check_soft_refs(session: Session, flush_context, instances) -> None:
    tenant = session.info.get("tenant_org")
    if not tenant:
        return        # jobs and platform tools: no shop to hold the write to (see audit SEC-05)
    wanted = _wanted(session, tenant)
    if not wanted:
        return
    pending = {(type(o), getattr(o, "id", None)): getattr(o, "org_id", None) for o in session.new}
    with session.no_autoflush:
        for (models, label), refs in wanted.items():
            owners = {}
            for model in models:
                for i in refs:
                    if (model, i) in pending:
                        owners.setdefault(i, set()).add(pending[(model, i)])
                missing = [i for i in refs if i not in owners]
                if missing:
                    # Plain columns with skip_tenant_filter: the point is to SEE another shop's owner
                    # and refuse, not to have the read guard hide it and call it unknown.
                    for i, org in session.execute(select(model.id, model.org_id).where(
                            model.id.in_(missing)).execution_options(skip_tenant_filter=True)).all():
                        owners.setdefault(i, set()).add(org)
            for ref_id, users in refs.items():
                for org, column in users:
                    if org not in owners.get(ref_id, ()):
                        from backend.services.owned import problem
                        raise problem(422, f"That {label} doesn't exist any more. Pick another.",
                                      f"{column}: no {label} with id {ref_id!r} in this shop",
                                      field=column.split(".", 1)[1])
