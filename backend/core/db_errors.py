"""A unique-index clash the app checks didn't catch (two saves at the same moment) becomes a plain
409 with a sentence, never a 500 (ii144a). Other integrity errors (a missing required value, a
broken reference) are real bugs and stay 500s.

SQLite names the index only for expression indexes ("UNIQUE constraint failed: index 'uq_…'") and
the columns otherwise ("… failed: product_barcodes.org_id, product_barcodes.barcode"); Postgres
names the constraint. Both forms are matched."""
from typing import Optional

from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

# (index name, SQLite column form) → message
_MESSAGES = (
    (("uq_product_barcodes_org_key", "product_barcodes.match_key"), "That barcode is already used by another product."),
    (("uq_parts_org_sku_active",), "That SKU is already used by another product."),
    (("uq_parts_org_mpn_active",), "That MPN is already used by another product"),
    (("uq_storage_bins_name",), "A bin with that name already exists in this warehouse."),
    (("uq_storage_bins_barcode", "storage_bins.barcode"), "That bin code is already used by another bin."),
    (("uq_serialized_items_live_serial",), "That serial number is already in stock."),
    (("uq_stock_levels_part_bin", "uq_stock_levels_part_loc_unbinned", "stock_levels.part_id"),
     "The stock changed while saving — please try again."),
    # Audit DB-04: "only one open at a time", backed by partial unique indexes (migration w2b01).
    (("uq_cash_shifts_open_location", "cash_shifts.location_id"),
     "A cash drawer is already open at this location — close it first."),
    (("uq_time_entries_running_employee", "time_entries.employee_id"),
     "You already have a timer running — stop it first."),
    (("uq_memberships_live_device_plan", "memberships.device_id"),
     "This asset already has a live membership on that plan."),
    # Audit DB-05 / FIN-17: two documents rendered the same number at the same moment.
    (("uq_sales_org_number", "sales.sale_number", "uq_stock_transfers_org_number", "stock_transfers.transfer_number",
      "uq_write_offs_org_number", "write_offs.number", "uq_purchase_returns_org_number", "purchase_returns.number",
      "uq_postings_org_number", "postings.number"),
     "That document number was just used — please try again."),
    # Audit DB-06
    (("uq_coupons_org_code", "coupons.code"), "A coupon with that code already exists."),
    (("uq_gift_cards_org_code", "gift_cards.code"), "A gift card with that code already exists."),
)
_UNIQUE_MARKERS = ("UNIQUE constraint failed", "duplicate key value violates unique constraint",
                   "UniqueViolation")


def unique_violation_message(exc: IntegrityError) -> Optional[str]:
    """The plain message for a unique clash, or None when this isn't one."""
    text = f"{exc.orig!s} {type(exc.orig).__name__}" if exc.orig is not None else str(exc)
    if not any(m in text for m in _UNIQUE_MARKERS):
        return None
    for needles, msg in _MESSAGES:
        if any(n in text for n in needles):
            return msg
    return "That value is already in use — refresh the page and try again."


async def integrity_error_handler(request: Request, exc: IntegrityError):
    msg = unique_violation_message(exc)
    if msg is None:
        raise exc
    return JSONResponse(status_code=409, content={"detail": msg})
