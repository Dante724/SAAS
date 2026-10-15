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
