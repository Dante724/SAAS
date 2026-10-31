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
    (("uq_product_barcodes_org_code", "product_barcodes.barcode"), "That barcode is already used by another product."),
    (("uq_parts_org_sku_active",), "That SKU is already used by another product."),
    (("uq_parts_org_mpn_active",), "That MPN is already used by another product"),
    (("uq_storage_bins_name",), "A bin with that name already exists in this warehouse."),
    (("uq_storage_bins_barcode", "storage_bins.barcode"), "That bin code is already used by another bin."),
    (("uq_serialized_items_live_serial",), "That serial number is already in stock."),
    (("uq_stock_levels_part_bin", "uq_stock_levels_part_loc_unbinned", "stock_levels.part_id"),
     "The stock changed while saving — please try again."),
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
