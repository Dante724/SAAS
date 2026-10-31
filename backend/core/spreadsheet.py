"""Spreadsheet export/import hygiene (CSV/XLSX formula injection, CWE-1236).

Client names, notes, product names and directory values are typed by staff — and, through the
portal and booking widget, by customers. Exported verbatim, a cell like `=HYPERLINK(...)` or
`@SUM(...)` runs as a formula when the accountant opens the file (openpyxl even stores a string
starting with "=" as a real formula). `safe_cell` neutralises such text with a leading
apostrophe; `unguard_cell` strips it again on import so exports round-trip.
"""
import re

_TRIGGERS = ("=", "+", "-", "@", "\t", "\r", "\n")
# Phone numbers and signed amounts ("+91 98200 11223", "-12.50") start with a trigger but
# can't call a function, so they're left readable.
_PLAIN_NUMBER = re.compile(r"[+-]?[\d\s().,-]+")


def safe_cell(v):
    if not isinstance(v, str) or not v or not v.startswith(_TRIGGERS):
        return v
    if _PLAIN_NUMBER.fullmatch(v):
        return v
    return "'" + v


def safe_row(row):
    return [safe_cell(v) for v in row]


def unguard_cell(v):
    if isinstance(v, str) and len(v) > 1 and v[0] == "'" and v[1:].startswith(_TRIGGERS):
        return v[1:]
    return v


# ── Import limits (audit L4) ──────────────────────────────────────────────────
# Every importer does per-row queries, so an unbounded file pinned a worker for minutes, and
# an .xlsx is a zip: a few hundred KB can inflate to gigabytes inside openpyxl.
MAX_IMPORT_ROWS = 10_000
MAX_XLSX_BYTES = 5 * 1024 * 1024
MAX_XLSX_UNCOMPRESSED = 50 * 1024 * 1024


def too_many_rows(n: int) -> str:
    return f"Too many rows ({n:,}); import at most {MAX_IMPORT_ROWS:,} at a time"


def open_xlsx(raw: bytes):
    """openpyxl workbook from untrusted bytes, refusing oversized files and zip bombs."""
    import io
    import zipfile

    import openpyxl
    from fastapi import HTTPException

    if len(raw) > MAX_XLSX_BYTES:
        raise HTTPException(413, f"File too large (max {MAX_XLSX_BYTES // (1024 * 1024)} MB)")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            if sum(i.file_size for i in z.infolist()) > MAX_XLSX_UNCOMPRESSED:
                raise HTTPException(413, "Spreadsheet expands too large to import")
    except zipfile.BadZipFile:
        raise HTTPException(422, "Not an .xlsx file")
    try:
        return openpyxl.load_workbook(io.BytesIO(raw), read_only=True)
    except Exception:
        raise HTTPException(422, "Could not read the spreadsheet")
