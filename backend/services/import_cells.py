"""Import centre — reading a spreadsheet and turning its cells into values.

Everything Excel does to a shop's data on the way in lives here, so every list importer gets the
same rules:

- ``read_sheet``: XLSX (openpyxl read-only, size + zip-bomb caps from core/spreadsheet) or CSV
  (BOM stripped; UTF-8, else the best-scoring of cp1252 / Mac Roman / latin-1; comma /
  semicolon / tab sniffed). Cells come back
  as text; a number Excel stored for a barcode keeps every digit ("4006381333931", never
  "4.00638E+12"); a date cell becomes YYYY-MM-DD; formula-guard apostrophes are removed.
- ``to_number``: decimal commas ("12,50", "1.234,56") as well as "1,234.56".
- ``to_code``: SKU / barcode text. Refuses a value Excel already turned into scientific notation
  (the digits are gone), and re-pads a UPC / EAN-8 that lost its leading zero when the check
  digit proves it.
- ``to_date``: ISO, Excel serial numbers (45292 = 2024-01-01) and day-first DD.MM.YYYY / DD/MM/YYYY.
"""
import csv
import io
import re
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import HTTPException

from backend.core.spreadsheet import (
    MAX_IMPORT_ROWS,
    MAX_XLSX_BYTES,
    open_xlsx,
    too_many_rows,
    unguard_cell,
)

MAX_IMPORT_COLS = 200      # a real sheet has a few dozen; a row of 2M commas would pad into gigabytes


def _too_wide() -> HTTPException:
    return HTTPException(413, f"Too many columns (max {MAX_IMPORT_COLS}) — delete the empty columns and try again")


class CellError(ValueError):
    """A cell that can't be read as its column's type. The message is shown to the user."""


# ── reading files ────────────────────────────────────────────────────────────
def _cell_text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, datetime):
        return v.date().isoformat() if (v.hour, v.minute, v.second) == (0, 0, 0) else v.isoformat(sep=" ")
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if v.is_integer() and abs(v) < 1e18:
            return str(int(v))          # a barcode stored as a number keeps every digit
        return repr(v)
    return str(unguard_cell(v)).strip()


# Council M14: Excel for Mac's default "Comma Separated Values (.csv)" writes Mac Roman, whose
# accented letters sit in the 0x80-0x9F bytes that cp1252 reads as symbols (é → Ž, ü → Ÿ) or
# doesn't define at all (ç, è, ù). After UTF-8, each 8-bit guess is scored on how much of that
# tell-tale junk it produces and the cleanest reading wins; latin-1 (every byte defined) is the
# last resort so a text file is never refused. A tie keeps the old order: cp1252 first.
_ODD_CHARS = {
    # cp1252 reading of Mac Roman accents (not ‘ ’ “ ” • – — … € ™, which real Windows text uses)
    "cp1252": set("ŽŸ‚ƒ„†‡ˆ‰Š‹ŒœšžŸ˜›"),
    # Mac Roman reading of cp1252 accents: math signs, bare diacritics and ligatures
    "mac_roman": set("√≈∆◊⁄ﬁﬂ‡·‚„‰ıˆ˜¯˘˙˚¸˝˛ˇ∞±≤≥∂∑∏π∫Ωæø¬≠"),
    "latin-1": set(),
}
_C1 = re.compile(r"[\x80-\x9f]")
_MIDWORD_CAPITAL = re.compile(r"[a-z][ÀÁÂÃÄÅÇÈÉÊËÌÍÎÏÑÒÓÔÕÖÙÚÛÜÝ]")   # "JosË", "MÁller": the wrong guess
ENCODING_LABELS = {"cp1252": "Windows text", "mac_roman": "Mac Roman", "latin-1": "Latin-1"}


def _oddness(text: str, enc: str) -> int:
    odd = _ODD_CHARS[enc]
    return (sum(1 for ch in text if ch in odd)
            + 3 * len(_C1.findall(text))
            + len(_MIDWORD_CAPITAL.findall(text)))


def decode_text(raw: bytes) -> tuple[str, str]:
    """(text, encoding) for a CSV upload: "utf-8" (BOM stripped), else the best of cp1252,
    mac_roman and latin-1 by score (see _ODD_CHARS)."""
    try:
        return raw.decode("utf-8-sig"), "utf-8"
    except UnicodeDecodeError:
        pass
    best = None
    for enc in ("cp1252", "mac_roman", "latin-1"):
        try:
            text = raw.decode(enc)
        except UnicodeDecodeError:
            continue
        score = _oddness(text, enc)
        if best is None or score < best[0]:
            best = (score, text, enc)
    if best is None:    # unreachable: latin-1 defines every byte
        raise HTTPException(422, "Could not read the file's text encoding — save it as CSV UTF-8")
    return best[1], best[2]


def encoding_note(enc: str | None) -> str | None:
    """What the preview says when the file wasn't UTF-8, so the shop checks the accents before
    committing (a guessed 8-bit encoding is a guess)."""
    if not enc or enc == "utf-8":
        return None
    return f"Read as {ENCODING_LABELS.get(enc, enc)} (not UTF-8) — check accents"


def _csv_rows(raw: bytes) -> tuple[list[list[str]], str]:
    text, enc = decode_text(raw)
    text = text.lstrip("﻿")
    if "\x00" in text:
        raise HTTPException(422, "That doesn't look like a CSV file")
    sample = text[:20000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    out, filled = [], 0
    for row in csv.reader(io.StringIO(text), dialect):
        while row and not row[-1].strip():
            row.pop()                   # trailing empty cells don't count as columns
        if len(row) > MAX_IMPORT_COLS:
            raise _too_wide()
        out.append([_cell_text(c) for c in row])
        filled += bool(row)
        if filled > MAX_IMPORT_ROWS + 1:
            raise HTTPException(413, too_many_rows(filled - 1))
    return out, enc


def _xlsx_rows(raw: bytes) -> list[list[str]]:
    wb = open_xlsx(raw)
    try:
        ws = wb.worksheets[0]
        out, filled = [], 0
        for row in ws.iter_rows(values_only=True, max_col=MAX_IMPORT_COLS + 1):
            cells = [_cell_text(v) for v in row]
            while cells and not cells[-1]:
                cells.pop()
            if len(cells) > MAX_IMPORT_COLS:
                raise _too_wide()
            out.append(cells)
            filled += bool(cells)
            if filled > MAX_IMPORT_ROWS + 1:
                raise HTTPException(413, too_many_rows(filled - 1))
        return out
    finally:
        wb.close()


def read_sheet(raw: bytes, file_name: str) -> tuple[list[str], list[list[str]], list[int]]:
    """(headers, data rows, sheet row number of each data row). Blank rows are dropped; the
    first non-blank row is the header."""
    return read_sheet_ex(raw, file_name)[:3]


def read_sheet_ex(raw: bytes, file_name: str) -> tuple[list[str], list[list[str]], list[int], Optional[str]]:
    """read_sheet plus the text encoding a CSV was read with ("utf-8", "cp1252", "mac_roman",
    "latin-1"; None for an xlsx), so the preview can say so."""
    if len(raw) > MAX_XLSX_BYTES:
        raise HTTPException(413, f"File too large (max {MAX_XLSX_BYTES // (1024 * 1024)} MB)")
    name = (file_name or "").lower()
    ext = name.rsplit(".", 1)[-1] if "." in name else ""
    if ext in ("xlsm", "xls", "xlsb", "ods", "numbers"):
        raise HTTPException(422, "Save the file as .xlsx or .csv first (macro and old Excel files aren't accepted)")
    enc = None
    if ext == "xlsx" or (ext not in ("csv", "txt", "tsv") and raw[:2] == b"PK"):
        grid = _xlsx_rows(raw)
    else:
        grid, enc = _csv_rows(raw)
    numbered = [(i + 1, r) for i, r in enumerate(grid) if any(c.strip() for c in r)]
    if not numbered:
        raise HTTPException(422, "The file is empty")
    if len(numbered) - 1 > MAX_IMPORT_ROWS:
        raise HTTPException(413, too_many_rows(len(numbered) - 1))
    _, head = numbered[0]
    width = max(len(r) for _, r in numbered)
    headers, seen = [], {}
    for i in range(width):
        h = (head[i] if i < len(head) else "").strip() or f"Column {i + 1}"
        if h in seen:
            seen[h] += 1
            h = f"{h} ({seen[h]})"
        else:
            seen[h] = 1
        headers.append(h)
    rows = [(r + [""] * (width - len(r)))[:width] for _, r in numbered[1:]]
    return headers, rows, [n for n, _ in numbered[1:]], enc


# ── header matching ──────────────────────────────────────────────────────────
def norm_header(h: str) -> str:
    h = re.sub(r"\s*\*\s*$", "", str(h or "")).lower()
    return re.sub(r"[^a-z0-9%]+", " ", h).strip()


# ── typed values ─────────────────────────────────────────────────────────────
_SCI = re.compile(r"^[+-]?\d+(?:[.,]\d+)?[eE][+-]?\d+$")
# Excel's display of a long number: "1.23457E+12" / "4E+12" — not a code like "100E5" typed as text
_EXCEL_SCI = re.compile(r"^[+-]?\d+(?:[.,]\d+)?[eE]\+\d+$|^[+-]?\d+[.,]\d+[eE]-?\d+$")


def to_number(s: str, label: str) -> float:
    raw = (s or "").strip()
    if _SCI.match(raw):
        return float(raw.replace(",", "."))
    t = re.sub(r"[\s$€£₹¥%'\u00a0]", "", raw)
    if not re.fullmatch(r"-?[\d.,]*\d[\d.,]*", t):
        raise CellError(f"{label} must be a number (got '{s}')")
    has_c, has_d = "," in t, "." in t
    if has_c and has_d:
        t = t.replace(".", "").replace(",", ".") if t.rfind(",") > t.rfind(".") else t.replace(",", "")
    elif has_c:
        parts = t.split(",")
        whole = parts[0].lstrip("-")
        # "1,234" / "12,345,678" are thousands; "12,50", "0,125", "1,2345" are decimal commas
        if len(parts) == 2 and (len(parts[1]) != 3 or whole in ("", "0")):
            t = parts[0] + "." + parts[1]
        else:
            t = t.replace(",", "")
    elif t.count(".") > 1:
        t = t.replace(".", "")          # "1.234.567"
    try:
        return float(t)
    except ValueError:
        raise CellError(f"{label} must be a number (got '{s}')")


def to_int(s: str, label: str) -> int:
    n = to_number(s, label)
    if not float(n).is_integer():
        raise CellError(f"{label} must be a whole number (got '{s}')")
    return int(n)


_YES = {"yes", "y", "true", "1", "x", "on"}
_NO = {"no", "n", "false", "0", "off", "-"}


def to_bool(s: str, label: str) -> bool:
    v = (s or "").strip().lower()
    if v in _YES:
        return True
    if v in _NO:
        return False
    raise CellError(f"{label} must be yes or no (got '{s}')")


def _gtin_ok(digits: str) -> bool:
    from backend.services.codes import gtin_check_digit
    return len(digits) in (8, 12, 13, 14) and gtin_check_digit(digits[:-1]) == digits[-1]


def to_code(s: str, label: str, *, barcode: bool = False) -> str:
    v = (s or "").strip()
    if _EXCEL_SCI.match(v):
        raise CellError(f"{label} '{v}' was turned into a number by Excel (E+) and lost digits — "
                        f"format the column as Text and type the code again")
    if barcode and v.isdigit() and len(v) in (7, 11) and _gtin_ok("0" + v):
        v = "0" + v                     # an EAN-8 / UPC-A that lost its leading zero
    return v


def code_key(v: Optional[str]) -> str:
    """Match key for a code: case-insensitive, and leading zeros don't matter for digit-only codes."""
    v = (v or "").strip()
    return (v.lstrip("0") or "0") if v.isdigit() else v.lower()


_EXCEL_EPOCH = date(1899, 12, 30)


def to_date(s: str, label: str) -> date:
    v = (s or "").strip()
    try:
        if re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", v):
            return date.fromisoformat("-".join(p.zfill(2) for p in v.split("-")))
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}[ T][\d:.]+(Z|[+-]\d{2}:?\d{2})?", v):
            return date.fromisoformat(v[:10])
        if re.fullmatch(r"\d{5}(\.\d+)?", v) and float(v) <= 2958465:   # Excel serial (10000 = 1927)
            return _EXCEL_EPOCH + timedelta(days=int(float(v)))
        m = re.fullmatch(r"(\d{1,2})[./-](\d{1,2})[./-](\d{4})", v)
        if m:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        pass
    raise CellError(f"{label} must be a date like 2024-03-15 (got '{s}')")


_SERIAL_SPLIT = re.compile(r"[,;\n\r|]+")


def to_serials(s: str) -> list[str]:
    return [x.strip() for x in _SERIAL_SPLIT.split(s or "") if x.strip()]


def digits(v: Optional[str]) -> str:
    return re.sub(r"\D", "", v or "")
