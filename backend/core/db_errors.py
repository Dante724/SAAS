"""A unique-index clash the app checks didn't catch (two saves at the same moment) becomes a plain
409 with a sentence, never a 500 (ii144a). Other integrity errors (a missing required value, a
broken reference) are real bugs and stay 500s.

SQLite names the index only for expression indexes ("UNIQUE constraint failed: index 'uq_…'") and
the columns otherwise ("… failed: product_barcodes.org_id, product_barcodes.barcode"); Postgres
names the constraint. Both forms are matched.

A value a column can't hold (numeric overflow, text too long, a NUL byte, an integer past the
column's range, a negative OFFSET/LIMIT) is a 422 with a sentence (re-review N3, final review QA-1).
On Postgres asyncpg raises these as a plain DBAPIError, not DataError, so the handler is registered
on DBAPIError and decides by the driver's SQLSTATE. Every other driver error (invalid input syntax,
a bad cast) is an app bug and stays a 500. A CHECK violation (23514) is a 422 with a generic
sentence (schema P2-2): request models should refuse such values first; this is the backstop."""
import logging
import re
from typing import Optional

from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DataError, DBAPIError, IntegrityError

log = logging.getLogger("tekly.db_errors")

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


CHECK_MESSAGE = "A value is outside the allowed range. Check quantities and amounts, then try again."


def check_violation_message(exc: IntegrityError) -> Optional[str]:
    """A generic sentence for a CHECK constraint violation (Postgres 23514, SQLite's text). The
    constraint name stays in the log, never in the reply."""
    orig = exc.orig
    state = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    if state == "23514" or "CHECK constraint failed" in str(orig if orig is not None else exc):
        return CHECK_MESSAGE
    return None


async def integrity_error_handler(request: Request, exc: IntegrityError):
    msg = unique_violation_message(exc)
    if msg is not None:
        return JSONResponse(status_code=409, content={"detail": msg})
    msg = check_violation_message(exc)
    if msg is not None:
        log.warning("CHECK violation mapped to 422: %s", str(exc.orig)[:200])
        return JSONResponse(status_code=422, content={"detail": msg})
    raise exc


# ── DataError: a value the column can't hold (re-review N3) ─────────────────
# Since DB-08 quantities, percentages, rates and points are NUMERIC(p, s); Postgres refuses a value
# past the precision ("numeric field overflow") where double precision took it. Request models bound
# these inputs (money.Qty/Pct/Rate/Points); this is the backstop for any path that still lets one
# through: a 422 with a sentence instead of a 500, and no SQL in the reply.
# Only the kinds below are the user's input. Any other error (invalid input syntax for a uuid or
# number, a bad cast, division by zero) means the app sent SQL it shouldn't have: it re-raises and
# stays a 500, so monitoring sees it.
#
# QA-1: on Postgres, SQLAlchemy's asyncpg adapter raises these as a plain DBAPIError (only
# IntegrityError gets its own class), so the handler is registered on DBAPIError too and decides by
# the driver's SQLSTATE (`exc.orig.sqlstate`), with the driver's text as the fallback.
# (Postgres SQLSTATE, driver-text needles) → message
_TOO_LARGE = "A number is too large to save. Check quantities, percentages and rates."
_DATA_MESSAGES = (
    ("22003", ("numeric field overflow", "numeric value out of range", "integer out of range"), _TOO_LARGE),
    ("22001", ("value too long for type",), "A text value is too long to save."),
    # QA-2 backstop: Postgres text can't hold a NUL byte (core.bad_input refuses most before routing).
    ("22021", ("invalid byte sequence for encoding",), "Text can't contain NUL characters (\\u0000)."),
    # QA-3 backstop: a negative skip / limit that reached SQL.
    ("2201X", ("offset must not be negative",), "Paging values can't be negative."),
    ("2201W", ("limit must not be negative",), "Paging values can't be negative."),
)
# asyncpg refuses an integer parameter past its column type before sending it, as SQLSTATE 22000
# (generic data exception) with this text. Only this shape is the user's number; any other 22000
# (say a str bound to an int) is an app bug and stays a 500.
_INT_PARAM_RANGE = re.compile(r"invalid input for query argument \$\d+: .*out of int(16|32|64) range")


def data_error_message(exc: DBAPIError) -> Optional[str]:
    """The plain message for a value the column can't hold, or None for any other driver error."""
    orig = exc.orig
    state = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    text = str(orig if orig is not None else exc)
    low = text.lower()
    for code, needles, msg in _DATA_MESSAGES:
        if state == code or any(n in low for n in needles):
            return msg
    if _INT_PARAM_RANGE.search(text):
        return _TOO_LARGE
    return None


async def data_error_handler(request: Request, exc: DBAPIError):
    """Registered for DataError and DBAPIError (IntegrityError keeps its own handler)."""
    msg = data_error_message(exc)
    if msg is None:
        raise exc
    log.warning("DB error mapped to 422: %s (%s)", msg,
                type(exc.orig).__name__ if exc.orig is not None else "no driver error")
    return JSONResponse(status_code=422, content={"detail": msg})


async def overflow_error_handler(request: Request, exc: OverflowError):
    """QA-3: an integer too big for the engine (SQLite binds at most int64) or for date maths
    (timedelta(days=10**20)) is the user's number, not a server fault."""
    log.warning("OverflowError mapped to 422: %s", str(exc)[:200])
    return JSONResponse(status_code=422, content={"detail": "A number is too large."})


def install_db_error_handlers(app) -> None:
    """Every bad-input → 4xx mapping for database and number errors, in one place."""
    app.add_exception_handler(IntegrityError, integrity_error_handler)   # ii144a 409, P2-2 CHECK 422
    app.add_exception_handler(DataError, data_error_handler)             # N3
    app.add_exception_handler(DBAPIError, data_error_handler)            # QA-1: asyncpg's path
    app.add_exception_handler(OverflowError, overflow_error_handler)     # QA-3
