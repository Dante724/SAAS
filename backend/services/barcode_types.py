"""Every barcode type Tekly can create, print and check (ii144b).

One table of types; for each, `normalize(kind, value)` validates the value (check digits where the
format has one) and returns the form that is stored and printed, and `svg(kind, value)` draws it.
Symbols are encoded by zxing-cpp (the same library family that phone cameras and the tests decode
with) and drawn here as plain SVG rects, so labels look the same as before (human-readable caption
under linear codes) and scale cleanly on any printer.

GS1 data — GS1-128 always, and Data Matrix / QR when the value is a GS1 string like
`(01)02000000000015(17)261231` — is encoded as GS1 (FNC1), so a scanner reads it as GS1 and
scan_codes.parse can split out GTIN, batch, expiry and serial.
"""
import re
from typing import Optional

from backend.services import scan_codes

# id: (label shown to people, linear?)
TYPES: dict[str, tuple[str, bool]] = {
    "ean13": ("EAN-13", True),
    "ean8": ("EAN-8", True),
    "upca": ("UPC-A", True),
    "upce": ("UPC-E", True),
    "itf14": ("ITF-14", True),
    "itf": ("Interleaved 2 of 5", True),
    "code39": ("Code 39", True),
    "code128": ("Code 128", True),
    "gs1_128": ("GS1-128", True),
    "qr": ("QR code", False),
    "datamatrix": ("Data Matrix", False),
    "pdf417": ("PDF417", False),
}
GTIN_TYPES = ("ean13", "ean8", "upca", "upce", "itf14")
# Generated (shop-made) codes: GTIN types from the in-store range; the rest carry an in-store EAN-13.
MAX_LEN = {"code39": 43, "code128": 80, "itf": 30, "qr": 500, "datamatrix": 500, "pdf417": 800, "gs1_128": 48}
_CODE39 = re.compile(r"^[0-9A-Z \-.$/+%]+$")


def label(kind: Optional[str]) -> str:
    return TYPES.get(kind or "", (kind or "", True))[0]


def _gtin(value: str, n: int, name: str) -> str:
    v = re.sub(r"[\s-]", "", value)
    if not scan_codes.is_digits(v):
        raise ValueError(f"{name} needs digits only")
    if len(v) == n - 1:
        return v + scan_codes.gtin_check(v)
    if len(v) != n:
        raise ValueError(f"{name} needs {n - 1} or {n} digits")
    if scan_codes.gtin_check(v[:-1]) != v[-1]:
        raise ValueError(f"{name} check digit is wrong (it should end in {scan_codes.gtin_check(v[:-1])})")
    return v


def normalize(kind: str, value: Optional[str]) -> str:
    """The value as stored and printed, or ValueError with a plain reason."""
    if kind not in TYPES:
        raise ValueError(f"Unknown barcode type {kind}")
    v = str(value or "").strip()
    if not v:
        raise ValueError("Enter a barcode")
    if kind == "ean13":
        return _gtin(v, 13, "EAN-13")
    if kind == "ean8":
        return _gtin(v, 8, "EAN-8")
    if kind == "upca":
        return _gtin(v, 12, "UPC-A")
    if kind == "itf14":
        return _gtin(v, 14, "ITF-14")
    if kind == "upce":
        d = re.sub(r"[\s-]", "", v)
        if not scan_codes.is_digits(d) or len(d) not in (6, 7, 8):
            raise ValueError("UPC-E needs 6 to 8 digits")
        if len(d) == 8 and d[0] in "01" and scan_codes.upce_to_upca(d[:7]) and not scan_codes.upce_to_upca(d):
            raise ValueError(f"UPC-E check digit is wrong (it should end in {scan_codes.upce_to_upca(d[:7])[-1]})")
        upca = scan_codes.upce_to_upca(d)
        if not upca:
            raise ValueError("UPC-E starts with 0 or 1 and has 6 data digits")
        body = d if len(d) == 8 else ("0" + d if len(d) == 6 else d)
        return body[:7] + upca[-1]
    if len(v) > MAX_LEN[kind]:
        raise ValueError(f"{label(kind)} holds at most {MAX_LEN[kind]} characters")
    if kind == "itf":
        if not scan_codes.is_digits(v) or len(v) % 2:
            raise ValueError("Interleaved 2 of 5 needs an even number of digits")
        return v
    if kind == "code39":
        up = v.upper()
        if not _CODE39.match(up):
            raise ValueError("Code 39 takes letters, digits, spaces and - . $ / + %")
        return up
    if kind == "code128":
        if any(not (32 <= ord(c) <= 126) for c in v):
            raise ValueError("Code 128 takes plain letters, digits and symbols")
        return v
    if kind == "gs1_128":
        ais = scan_codes.parse_gs1(v)
        if not ais:
            raise ValueError("GS1-128 needs GS1 data, like (01)02000000000015(17)261231(10)LOT1")
        g = dict(ais).get("01")
        if g and not scan_codes.valid_gtin(g):
            raise ValueError("The (01) GTIN check digit is wrong")
        return scan_codes.to_hri(ais)
    # 2D: any text; a GS1 string is kept in its bracketed form so it's encoded as GS1
    if v.startswith("("):
        ais = scan_codes.parse_gs1(v)
        if ais:
            return scan_codes.to_hri(ais)
    return v


def is_gs1(kind: str, value: str) -> bool:
    return kind == "gs1_128" or (kind in ("qr", "datamatrix") and value.startswith("(")
                                 and scan_codes.parse_gs1(value) is not None)


def _zx_format(kind: str):
    import zxingcpp as z
    return {"ean13": z.BarcodeFormat.EAN13, "ean8": z.BarcodeFormat.EAN8, "upca": z.BarcodeFormat.UPCA,
            "upce": z.BarcodeFormat.UPCE, "itf14": z.BarcodeFormat.ITF14, "itf": z.BarcodeFormat.ITF,
            "code39": z.BarcodeFormat.Code39, "code128": z.BarcodeFormat.Code128,
            "gs1_128": z.BarcodeFormat.Code128, "qr": z.BarcodeFormat.QRCode,
            "datamatrix": z.BarcodeFormat.DataMatrix, "pdf417": z.BarcodeFormat.PDF417}[kind]


def encode(kind: str, value: str):
    """A zxing-cpp Barcode for the (already normalised) value."""
    import zxingcpp as z
    payload = value
    if kind == "upce":
        payload = value[:7]          # zxing computes and checks the check digit itself
    elif kind in ("ean13", "ean8", "upca", "itf14"):
        payload = value[:-1]
    kw = {"gs1": True} if is_gs1(kind, value) else {}
    return z.create_barcode(payload, _zx_format(kind), **kw)


def modules(kind: str, value: str) -> list[list[bool]]:
    """Rows of modules (True = dark), without quiet zones. A linear code is one row."""
    import zxingcpp as z
    img = z.write_barcode_to_image(encode(kind, value), scale=1, add_quiet_zones=False)
    mv = memoryview(img)
    h, w = mv.shape[0], mv.shape[1]
    data = bytes(mv)
    rows = [[data[r * w + c] < 128 for c in range(w)] for r in range(h)]
    return [rows[h // 2]] if TYPES[kind][1] else rows     # middle row: ITF-14 bearer bars fill the edges


def svg(kind: str, value: str, *, height: int = 48, module: int = 2) -> str:
    """The symbol as a standalone SVG. Raises ValueError for a value the type can't hold."""
    v = normalize(kind, value)
    rows = modules(kind, v)
    if TYPES[kind][1]:
        quiet = 10 * module
        bars, x0 = [], None
        row = rows[0] + [False]
        for i, dark in enumerate(row):
            if dark and x0 is None:
                x0 = i
            elif not dark and x0 is not None:
                bars.append(f"<rect x='{quiet + x0 * module}' y='0' width='{(i - x0) * module}' height='{height}' fill='#000'/>")
                x0 = None
        w = quiet * 2 + len(rows[0]) * module
        cap = _xml(v)
        return (f"<svg xmlns='http://www.w3.org/2000/svg' width='{w}' height='{height + 18}' "
                f"viewBox='0 0 {w} {height + 18}' shape-rendering='crispEdges'>"
                f"<rect width='{w}' height='{height + 18}' fill='#fff'/>{''.join(bars)}"
                f"<text x='{w / 2}' y='{height + 14}' font-size='11' font-family='monospace' "
                f"text-anchor='middle' fill='#000'>{cap}</text></svg>")
    m = 3 if kind != "pdf417" else 2
    quiet = 2 * m
    rects = []
    for r, row in enumerate(rows):
        c = 0
        while c < len(row):
            if row[c]:
                start = c
                while c < len(row) and row[c]:
                    c += 1
                rects.append(f"<rect x='{quiet + start * m}' y='{quiet + r * m}' width='{(c - start) * m}' "
                             f"height='{m}' fill='#000'/>")
            else:
                c += 1
    w = quiet * 2 + len(rows[0]) * m
    h = quiet * 2 + len(rows) * m
    return (f"<svg xmlns='http://www.w3.org/2000/svg' width='{w}' height='{h}' viewBox='0 0 {w} {h}' "
            f"shape-rendering='crispEdges'><rect width='{w}' height='{h}' fill='#fff'/>{''.join(rects)}</svg>")


def generated_value(kind: str, in_store_ean13: str) -> str:
    """The shop-made value for a type, from a fresh in-store EAN-13: GTIN types use the in-store GTIN
    in their own length; GS1 types carry it as (01); the rest carry the 13 digits."""
    if kind == "itf14":
        body = ("0" + in_store_ean13)[:13]
        return body + scan_codes.gtin_check(body)
    if kind == "itf":                  # needs an even number of digits: the GTIN-14 form (same key)
        return "0" + in_store_ean13
    if kind in ("gs1_128",):
        return f"(01){in_store_ean13.zfill(14)}"
    return in_store_ean13


def _xml(s: str) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
