"""Schema rules from the 2026-11-12 backend audit, held by the models (and so by every database
the migrations build: test_144eo compares the two).

- DB-03: money and stock rows point at their parents by (org_id, id), so the database itself
  refuses a payment, job line or stock row that names another shop's record, whatever code path
  (ORM, Core, raw SQL) writes it.
- DB-10: every foreign-key column has an index that starts with it, or is allow-listed here with
  the reason it doesn't need one.
- DB-12: money children never disappear with their parent (no ORM delete cascade, no ON DELETE
  CASCADE).
- DB-13: every ``*_id`` column is a real foreign key, or is allow-listed here with the reason
  (polymorphic, external, circular...).
- DB-14: devices and purchase orders always have a shop.
- DB-15: a unique key on a shop's table includes org_id, or is allow-listed as deliberately global.
- DB-17: no index that another index on the same table already covers.

Each allow-list can only shrink: an entry that no longer applies fails the test.
"""
import uuid

import pytest
from sqlalchemy import ForeignKeyConstraint, Index, UniqueConstraint, insert, select
from sqlalchemy.exc import IntegrityError

from backend.db.database import Base
from backend.models import all_models  # noqa: F401 — registers every table

T = Base.metadata.tables

# ── DB-03 ────────────────────────────────────────────────────────────────────────────────────────
# (child table, column, parent table): the child's (org_id, column) must exist in parent (org_id, id).
TENANT_FKS = [
    ("payments", "order_id", "orders"), ("payments", "invoice_id", "invoices"),
    ("payments", "account_id", "accounts"), ("payments", "client_id", "clients"),
    ("payments", "shift_id", "cash_shifts"), ("payments", "received_by_id", "employees"),
    ("payments", "voided_by_id", "employees"),
    ("transactions", "account_id", "accounts"), ("transactions", "shift_id", "cash_shifts"),
    ("transactions", "cash_flow_item_id", "cash_flow_items"), ("transactions", "employee_id", "employees"),
    ("transactions", "voided_by_id", "employees"),
    ("order_lines", "order_id", "orders"), ("order_lines", "order_item_id", "order_items"),
    ("order_lines", "technician_id", "employees"), ("order_lines", "discount_by_id", "employees"),
    ("invoice_lines", "invoice_id", "invoices"), ("invoice_lines", "order_line_id", "order_lines"),
    ("sale_lines", "sale_id", "sales"),
    ("stock_movements", "part_id", "parts"), ("stock_movements", "location_id", "locations"),
    ("stock_movements", "warehouse_id", "warehouses"), ("stock_movements", "bin_id", "storage_bins"),
    ("stock_movements", "created_by_id", "employees"),
    ("stock_levels", "part_id", "parts"), ("stock_levels", "location_id", "locations"),
    ("stock_levels", "warehouse_id", "warehouses"), ("stock_levels", "bin_id", "storage_bins"),
    # the parents' own links the audit names first
    ("orders", "client_id", "clients"), ("orders", "device_id", "devices"), ("orders", "location_id", "locations"),
    ("invoices", "order_id", "orders"), ("invoices", "client_id", "clients"),
    ("stock_reservations", "part_id", "parts"),
]


def _org_fk(child: str, col: str):
    for c in T[child].constraints:
        if isinstance(c, ForeignKeyConstraint) and [x.name for x in c.columns] == ["org_id", col]:
            return c
    return None


def _unique_org_id_id(parent: str) -> bool:
    for ix in T[parent].indexes:
        if ix.unique and [getattr(e, "name", None) for e in ix.expressions] == ["org_id", "id"] \
                and ix.dialect_options["postgresql"].get("where") is None:
            return True
    return any(isinstance(c, UniqueConstraint) and [x.name for x in c.columns] == ["org_id", "id"]
               for c in T[parent].constraints)


@pytest.mark.parametrize("child,col,parent", TENANT_FKS)
def test_money_and_stock_rows_reference_their_parent_by_shop_and_id(child, col, parent):
    fk = _org_fk(child, col)
    assert fk is not None, f"{child}({col}) needs FOREIGN KEY (org_id, {col}) -> {parent}(org_id, id)"
    assert [e.target_fullname for e in fk.elements] == [f"{parent}.org_id", f"{parent}.id"]
    assert not fk.ondelete, "a cross-shop key never cascades"
    assert _unique_org_id_id(parent), f"{parent} needs a UNIQUE (org_id, id) for the key to reference"
    assert not T[parent].c.org_id.nullable


def test_the_migration_builds_exactly_these_keys():
    from backend.tests.test_dbi_migrations import migration
    dbi01 = migration("dbi01_tenant_keys_and_indexes")
    assert sorted(dbi01.TENANT_FKS) == sorted(TENANT_FKS)
    assert sorted(dbi01.TENANT_PARENTS) == sorted({p for _c, _col, p in TENANT_FKS})


async def _two_shops(db):
    from backend.models import Client, Location, Order, Organisation
    ids = {}
    for k in ("A", "B"):
        org = Organisation(name=f"Shop {k}", account_id=f"TK-{uuid.uuid4().hex[:8]}")
        db.add(org)
        await db.flush()
        loc = Location(org_id=org.id, name="Main")
        cl = Client(org_id=org.id, name=f"Client {k}")
        db.add_all([loc, cl])
        await db.flush()
        o = Order(org_id=org.id, client_id=cl.id, location_id=loc.id, order_number=f"W-{k}")
        db.add(o)
        await db.flush()
        ids[k] = {"org": org.id, "loc": loc.id, "client": cl.id, "order": o.id}
    await db.commit()
    return ids


async def test_core_insert_of_a_payment_naming_another_shops_client_is_refused(db):
    """The audit's probe: a Core insert never reaches the ORM flush guard. The database refuses it."""
    from backend.models import Payment
    s = await _two_shops(db)
    with pytest.raises(IntegrityError):
        await db.execute(insert(Payment).values(id=str(uuid.uuid4()), org_id=s["A"]["org"], order_id=s["A"]["order"],
                                                client_id=s["B"]["client"], method="cash", amount=10))
    await db.rollback()
    # the same payment with its own shop's client is fine
    await db.execute(insert(Payment).values(id=str(uuid.uuid4()), org_id=s["A"]["org"], order_id=s["A"]["order"],
                                            client_id=s["A"]["client"], method="cash", amount=10))
    await db.commit()


async def test_core_insert_of_stock_and_job_rows_across_shops_is_refused(db):
    from backend.models import OrderLine, Part, StockMovement
    s = await _two_shops(db)
    part_b = Part(org_id=s["B"]["org"], name="B's screen")
    db.add(part_b)
    await db.commit()
    with pytest.raises(IntegrityError):
        await db.execute(insert(StockMovement).values(id=str(uuid.uuid4()), org_id=s["A"]["org"], part_id=part_b.id,
                                                      location_id=s["A"]["loc"], movement_type="adjust", qty=1))
    await db.rollback()
    with pytest.raises(IntegrityError):     # a line in A's books on B's job
        await db.execute(insert(OrderLine).values(id=str(uuid.uuid4()), org_id=s["A"]["org"],
                                                  order_id=s["B"]["order"], line_type="labour", description="x"))
    await db.rollback()


# ── DB-13: every *_id is a real FK unless listed here ───────────────────────────────────────────
POLY = "polymorphic: which table it points at depends on a sibling *_type/kind column"
EXTERNAL = "an id in another system (Stripe, Twilio, QuickBooks, a webhook sender), not a row of ours"
NUMBER = "a business number (tax/VAT id), not a reference"
CIRCULAR = ("organisations <-> a table whose org_id references organisations: an FK would make the pair "
            "circular (DB-17); the settings endpoints check the id belongs to the shop")
LOG = "a migration's own log/backup table: it must outlive the rows it describes"
SOFT_REFS = {
    # polymorphic references (backend/db/tenancy._SOFT_REFS checks the catalog ones on write)
    "order_lines.ref_id": POLY, "sale_lines.ref_id": POLY, "recommendations.ref_id": POLY,
    "bundle_components.ref_id": POLY, "transactions.ref_id": POLY, "stock_movements.ref_id": POLY,
    "serial_events.ref_id": POLY, "client_ledger_entries.ref_id": POLY, "loyalty_ledger.ref_id": POLY,
    "gift_card_ledger.ref_id": POLY, "employee_balance_ledger.ref_id": POLY, "approval_requests.ref_id": POLY,
    "audit_log.entity_id": POLY, "event_feed_items.entity_id": POLY, "taggables.entity_id": POLY,
    "tax_exceptions.target_id": POLY, "price_type_items.item_id": POLY, "merge_logs.target_id": POLY,
    "invoice_lines.source_id": POLY, "custom_fields.upsell_ref_id": POLY,
    "checklist_template_items.upsell_ref_id": POLY, "order_checklist_items.upsell_ref_id": POLY,
    "recommendations.source_ref_id": POLY, "integration_mappings.local_id": POLY,
    "integration_sync_logs.local_id": POLY,
    "order_lines.tax_id": ("a snapshot of the tax applied when the line was priced: a tax can be deleted "
                           "(services/tax.py) and the line keeps its rate; the tenant guard checks it on write"),
    "message_handoffs.template_id": "free label of whichever kind of template (message or notification) was used",
    "form_layouts.type_id": "'' means 'no work-order type' and is part of uq_form_layout; NULL would break the key",
    "payments.refund_id": ("refunds.payment_id already points here; a refunds <-> payments cycle can't be "
                           "created on SQLite"),
    "order_lines.bundle_group_id": "a group label shared by a kit's lines, not a row id",
    "transactions.transfer_pair_id": "a label shared by the two halves of a transfer, not a row id",
    "membership_assets.plan_id": ("w4s01: a copy of memberships.plan_id kept by a trigger, only for the "
                                  "one-live-membership-per-plan-per-asset index; memberships.plan_id is the FK"),
    # external ids
    "integration_mappings.remote_id": EXTERNAL, "integration_sync_logs.remote_id": EXTERNAL,
    "organisations.stripe_customer_id": EXTERNAL, "organisations.stripe_subscription_id": EXTERNAL,
    "organisations.stripe_subscription_item_id": EXTERNAL, "organisations.stripe_seat_item_id": EXTERNAL,
    "organisations.stripe_price_id": EXTERNAL, "payment_webhook_events.event_id": EXTERNAL,
    "qbo_connections.realm_id": EXTERNAL, "xero_connections.tenant_id": EXTERNAL,
    "sso_connections.client_id": EXTERNAL, "calls.external_id": EXTERNAL,
    "ecommerce_order_maps.external_order_id": EXTERNAL, "inbox_threads.external_id": EXTERNAL,
    "inbox_messages.external_id": EXTERNAL, "chat_messages.external_id": EXTERNAL,
    "payment_intents.provider_link_id": EXTERNAL, "terminal_charges.provider_payment_id": EXTERNAL,
    "payment_terminals.reader_id": EXTERNAL,
    "organisations.account_id": "the shop's own readable account number (TK-…), not a reference",
    # business numbers
    "legal_entities.tax_id": NUMBER, "employees.tax_id": NUMBER, "clients.tax_id": NUMBER,
    # circular with organisations
    "organisations.owner_id": CIRCULAR, "organisations.price_type_tickets_id": CIRCULAR,
    "organisations.price_type_sales_id": CIRCULAR, "organisations.price_type_invoices_id": CIRCULAR,
    "organisations.cash_category_tickets_id": CIRCULAR, "organisations.cash_category_sales_id": CIRCULAR,
    # migration logs
    "role_permission_backups.org_id": LOG, "tax_stack_backfill_log.org_id": LOG,
    "tax_stack_backfill_log.tax_id": LOG,
}


def test_every_id_column_is_a_foreign_key_or_listed_with_a_reason():
    soft = {f"{t.name}.{c.name}" for t in Base.metadata.sorted_tables for c in t.columns
            if c.name.endswith("_id") and not c.foreign_keys and not c.primary_key}
    new = sorted(soft - set(SOFT_REFS))
    stale = sorted(set(SOFT_REFS) - soft)
    assert not new, ("these *_id columns reference nothing: add a ForeignKey (and a migration with an orphan "
                     "pre-check, see dbi01), or list them in SOFT_REFS with the reason:\n  " + "\n  ".join(new))
    assert not stale, f"now a real FK (or gone): remove from SOFT_REFS: {stale}"


# ── DB-10: every FK column has a leading index unless listed here ───────────────────────────────
ACTOR = ("who did it (created/approved/voided by…): shown on the record, never filtered on; employees "
         "are deactivated, never deleted, so no parent delete scans it")
SETTING = "a small per-shop settings table (tens of rows): every read is a scan of the shop's rows anyway"
CATALOG = ("points at a settings/catalog row (location, role, price list, template, status…) that is never "
           "deleted while in use; reads filter by org_id or the parent document first")
BLOB = "a media blob loaded by this row; blobs are never looked up the other way"
NEVER_DELETED = ("points at a row that is archived or voided, never deleted (orders, coupons, conversions): "
                 "no parent delete scans it, and nothing filters on it alone")
FK_WITHOUT_INDEX = {
    "appointments.employee_id": "read with the shop: ix_appointments_org_id_employee_id (org_id, employee_id)",
    **{k: NEVER_DELETED for k in ("orders.converted_from_id", "orders.converted_order_id", "sales.coupon_id",
                                  "asset_product_conversions.reversed_by_id")},
    **{k: ACTOR for k in ("admin_assignment_log.by_admin_id", "admin_assignment_log.from_admin_id",
                          "admin_assignment_log.to_admin_id", "admin_shop_notes.admin_id")},
    **{k: SETTING for k in ("directory_items.parent_id", "locations.price_type_id")},
    **{k: ACTOR for k in (
        "api_keys.created_by_id", "approval_requests.requested_by_id", "approval_requests.decided_by_id",
        "cash_shifts.opened_by_id", "currency_rate_history.changed_by", "custom_reports.created_by_id",
        "event_feed_items.author_id", "expenses.recorded_by_id", "import_batches.actor_id", "invites.invited_by",
        "merge_logs.created_by_id", "payroll_runs.voided_by_id", "processor_payouts.created_by_id",
        "cash_drawer_movements.created_by_id", "client_ledger_entries.created_by_id", "consent_log.actor_id",
        "sales.voided_by_id", "transactions.voided_by_id", "postings.created_by_id",
        "purchase_returns.created_by_id", "stock_transfers.created_by_id", "write_offs.created_by_id",
        "stocktakes.created_by_id", "orders.archived_by_id", "asset_movements.created_by_id",
        "client_returns.created_by_id", "estimate_revisions.created_by_id", "message_handoffs.actor_id",
        "order_checklists.author_id", "order_comments.author_id", "order_status_logs.changed_by_id",
        "credit_notes.created_by_id", "credit_notes.issued_by_id", "credit_notes.canceled_by_id",
        "inbox_messages.author_id", "order_checklist_items.done_by_id", "recommendations.author_id",
        "chat_messages.author_id", "refunds.voided_by_id", "terminal_charges.created_by_id",
        "time_entries.approved_by_id", "asset_product_conversions.created_by_id", "serial_events.created_by_id",
        "time_entry_audits.actor_id", "calls.employee_id", "memberships.sold_by_id")},
    **{k: SETTING for k in (
        "custom_fields.type_id", "label_templates.price_type_id", "locations.legal_entity_id",
        "tax_exceptions.tax_id", "tax_rules.tax_id", "accounts.location_id", "checkin_points.location_id",
        "checkin_points.terms_template_id", "employees.location_id", "employees.role_id",
        "membership_plan_items.service_id", "notification_rules.template_id", "notification_rules.location_id",
        "order_statuses.location_id", "resources.location_id", "feedback_settings.trigger_status_id",
        "invites.role_id", "invites.location_id", "saved_filters.employee_id", "status_transitions.from_status_id",
        "status_transitions.to_status_id", "status_transitions.role_id", "work_schedules.location_id",
        "warehouses.default_bin_id", "ecommerce_integrations.location_id", "ecommerce_integrations.warehouse_id",
        "import_mappings.supplier_id", "commission_rules.employee_id", "commission_rules.wo_type_id",
        "commission_rules.product_id", "commission_rules.service_id", "task_overdue_alerts.employee_id",
        "wage_records.employee_id")},
    **{k: CATALOG for k in (
        "clients.disc_services_price_type_id", "clients.disc_materials_price_type_id", "expenses.location_id",
        "payroll_runs.account_id", "shifts.location_id", "parts.default_supplier_id", "parts.asset_group_id",
        "postings.account_id", "purchase_returns.account_id", "devices.converted_to_part_id",
        "devices.asset_group_id", "posting_lines.price_type_id", "stocktakes.location_id", "stocktakes.category_id",
        "employee_balance_ledger.rule_id", "memberships.manager_id", "orders.resource_id",
        "appointments.location_id", "appointments.service_id", "client_returns.location_id",
        "generated_documents.template_id", "inbox_threads.assigned_to_id", "invoices.legal_entity_id",
        "leads.location_id", "leads.manager_id", "leads.lead_type_id", "membership_usage.plan_item_id",
        "membership_usage.device_id", "message_handoffs.client_id", "order_checklists.template_id",
        "purchase_orders.supplier_id", "purchase_orders.location_id", "stock_reservations.location_id",
        "chat_threads.lead_id", "checkins.location_id", "checkins.point_id", "checkins.job_type_id",
        "checkins.converted_order_id", "stock_item_batches.location_id", "stock_item_batches.po_id",
        "payment_intents.account_id", "serialized_items.location_id", "serialized_items.converted_asset_id",
        "serialized_items.supplier_id", "terminal_charges.account_id", "time_entries.location_id",
        "asset_product_conversions.serialized_item_id", "asset_product_conversions.asset_group_id",
        "purchase_return_lines.serialized_item_id", "serial_events.location_id", "serial_events.bin_id",
        "stocktake_lines.serialized_item_id", "write_off_lines.serialized_item_id", "stocktake_serials.line_id",
        "stocktake_serials.serialized_item_id", "sales.location_id",
        "stock_transfers.from_location_id", "stock_transfers.to_location_id",
        "stock_transfers.from_warehouse_id", "stock_transfers.to_warehouse_id")},
    **{k: BLOB for k in ("event_feed_items.media_blob_id", "parts.image_blob_id", "orders.signature_blob_id",
                         "purchase_orders.supplier_invoice_blob_id")},
}


def _leading(table) -> set:
    lead = {table.primary_key.columns.values()[0].name} if table.primary_key.columns else set()
    for ix in table.indexes:
        first = list(ix.expressions)[0] if ix.expressions else None
        if getattr(first, "name", None) and getattr(first, "table", None) is table:
            lead.add(first.name)
    for c in table.constraints:
        if isinstance(c, UniqueConstraint) and c.columns:
            lead.add(list(c.columns)[0].name)
    return lead


def test_every_foreign_key_column_has_a_leading_index_or_is_listed_with_a_reason():
    unindexed = set()
    for t in Base.metadata.sorted_tables:
        lead = _leading(t)
        unindexed |= {f"{t.name}.{c.name}" for c in t.columns if c.foreign_keys and c.name not in lead}
    new = sorted(unindexed - set(FK_WITHOUT_INDEX))
    stale = sorted(set(FK_WITHOUT_INDEX) - unindexed)
    assert not new, ("these FK columns have no index starting with them: add index=True (and the index to a "
                     "migration, CONCURRENTLY on Postgres), or list them in FK_WITHOUT_INDEX with the reason:\n  "
                     + "\n  ".join(new))
    assert not stale, f"now indexed (or gone): remove from FK_WITHOUT_INDEX: {stale}"


def test_the_hot_foreign_keys_the_audit_named_are_indexed():
    for t, c in (("tasks", "assigned_to_id"), ("tasks", "created_by_id"), ("tasks", "parent_task_id"),
                 ("tasks", "location_id"), ("sales", "seller_id"), ("sales", "account_id"),
                 ("clients", "manager_id"), ("payments", "voided_by_id")):
        assert c in _leading(T[t]), f"{t}.{c}"


# ── DB-12: money children never cascade-delete ──────────────────────────────────────────────────
MONEY_CHILDREN = {"order_lines", "order_status_logs", "order_comments", "invoice_lines", "invoice_orders",
                  "po_lines", "credit_note_lines", "payments", "transactions", "sale_lines", "refunds",
                  "serial_events"}


def test_no_orm_delete_cascade_onto_money_or_history_rows():
    from sqlalchemy.orm import configure_mappers
    configure_mappers()
    bad = [f"{m.class_.__name__}.{r.key}" for m in Base.registry.mappers for r in m.relationships
           if r.mapper.local_table.name in MONEY_CHILDREN and (r.cascade.delete or r.cascade.delete_orphan)]
    assert not bad, f"delete/delete-orphan cascade onto money/history rows: {bad}"


def test_no_on_delete_cascade_onto_money_or_history_rows():
    bad = [f"{t}.{fk.parent.name}" for t in MONEY_CHILDREN for fk in T[t].foreign_keys
           if (fk.ondelete or "").upper() == "CASCADE"]
    assert not bad, f"ON DELETE CASCADE on money/history rows: {bad}"


async def test_deleting_a_job_with_lines_is_refused_not_cascaded(db):
    from backend.models import Order, OrderLine
    s = await _two_shops(db)
    db.add(OrderLine(org_id=s["A"]["org"], order_id=s["A"]["order"], line_type="part", description="Screen",
                     qty=1, unit_price=10))
    await db.commit()
    order = (await db.execute(select(Order).where(Order.id == s["A"]["order"]))).scalar_one()
    await db.delete(order)
    with pytest.raises(IntegrityError):
        await db.commit()
    await db.rollback()
    assert (await db.execute(select(OrderLine.id).where(OrderLine.order_id == s["A"]["order"]))).first()


# ── DB-14 ────────────────────────────────────────────────────────────────────────────────────────
def test_devices_and_purchase_orders_always_have_a_shop():
    assert not T["devices"].c.org_id.nullable
    assert not T["purchase_orders"].c.org_id.nullable


# ── DB-15: unique keys on a shop's tables include org_id unless listed ──────────────────────────
GLOBAL_UNIQUE = {
    "employees.uq_employees_email": "sign-in identity: one email signs in to one shop (owner decision)",
    "employees.uq_employees_email_lower": "sign-in identity, case-insensitive (same decision)",
    "orders.uq_orders_barcode": "144ei: a work-order code is unique across every shop (Orderry), so a scan "
                                "or a label can never resolve to another shop's job",
    "orders.ix_orders_public_token": "tracking-link token (legacy plaintext column, emptied by SEC-12)",
    "orders.uq_orders_public_token_hash": "tracking-link token hash: the link carries no shop",
    "payment_terminals.uq_payment_terminal_live_reader": "a physical card reader belongs to one shop; "
                                                         "provider webhooks find the shop by reader id",
    "auth_sessions.ix_auth_sessions_sid": "session token", "client_portal_links.ix_client_portal_links_token_hash":
    "portal-link token hash", "checkin_points.uq_checkin_points_token": "kiosk token",
    "payment_webhook_events.uq_payment_webhook_event": "provider event ids are global to the provider",
}
# keys made of other unique ids (a row id is already unique across shops): no org needed
_ID_KEYS = {"automation_fires", "price_type_items", "taggables", "user_prefs", "cash_shifts", "storage_bins",
            "stock_levels", "memberships", "membership_assets", "membership_usage", "order_items", "checkins",
            "invoice_orders", "chat_messages", "payment_intents", "task_overdue_alerts", "terminal_charges",
            "time_entries", "stocktake_serials"}


def _org_scoped_uniques():
    for t in Base.metadata.sorted_tables:
        if "org_id" not in t.c or t.name in _ID_KEYS:
            continue
        keys = [(ix.name, [getattr(e, "name", str(e)) for e in ix.expressions]) for ix in t.indexes if ix.unique]
        keys += [(c.name, [x.name for x in c.columns]) for c in t.constraints if isinstance(c, UniqueConstraint)]
        for c in t.columns:
            if c.unique and not c.index:
                keys.append((f"uq_{t.name}_{c.name}", [c.name]))
        for name, cols in keys:
            if "org_id" not in cols:
                yield f"{t.name}.{name}"


def test_unique_keys_on_a_shops_tables_are_per_shop_unless_listed():
    found = set(_org_scoped_uniques())
    new = sorted(found - set(GLOBAL_UNIQUE))
    stale = sorted(set(GLOBAL_UNIQUE) - found)
    assert not new, "unique across every shop: scope it (org_id, …) or list it with the reason:\n  " + "\n  ".join(new)
    assert not stale, f"no longer a global unique: remove from GLOBAL_UNIQUE: {stale}"


# ── DB-17: no index already covered by another ──────────────────────────────────────────────────
def test_no_index_is_a_prefix_of_another_index_on_the_same_table():
    dup = []
    for t in Base.metadata.sorted_tables:
        plain = []
        for ix in t.indexes:
            if ix.dialect_options["postgresql"].get("where") is not None or ix.dialect_options["sqlite"].get("where") is not None:
                continue
            names = [getattr(e, "name", None) for e in ix.expressions]
            if None in names:
                continue
            plain.append((ix.name, names, ix.unique))
        plain += [(c.name, [x.name for x in c.columns], True) for c in t.constraints if isinstance(c, UniqueConstraint)]
        for name, cols, uq in plain:
            if uq:
                continue          # a unique index is a rule, not just a lookup path
            for other, ocols, _ouq in plain:
                if other != name and len(ocols) >= len(cols) and ocols[:len(cols)] == cols:
                    dup.append(f"{t.name}.{name} {cols} is covered by {other} {ocols}")
                    break
    assert not dup, "drop these redundant indexes (DB-17):\n  " + "\n  ".join(dup)
