"""Barcode & QR rendering as self-contained inline SVG.

- EAN-13 / UPC-A (retail GTINs) with check-digit validation; a 12/11-digit body gets its
  check digit appended, a full-length one must carry the right digit.
- Code128 (subset B, auto-C for long digit runs) drawn as vector bars so it stays
  scannable at any print resolution — no raster, no external service.
- QR via `segno` (pure-python, zero runtime deps). If segno is unavailable the QR
  helper falls back to a Code128 barcode of the same data so documents never break.

Both return an `<svg>…</svg>` string ready to drop straight into a template.
"""
from __future__ import annotations

# ── Code128 ──────────────────────────────────────────────────────────────────
# The 107 symbol patterns (values 0–106). Each is 6 widths (bar,space,…) summing
# to 11 modules; the stop pattern (106) carries the extra 2-module bar.
_CODE128_PATTERNS = [
    "212222", "222122", "222221", "121223", "121322", "131222", "122213", "122312",
    "132212", "221213", "221312", "231212", "112232", "122132", "122231", "113222",
    "123122", "123221", "223211", "221132", "221231", "213212", "223112", "312131",
    "311222", "321122", "321221", "312212", "322112", "322211", "212123", "212321",
    "232121", "111323", "131123", "131321", "112313", "132113", "132311", "211313",
    "231113", "231311", "112133", "112331", "132131", "113123", "113321", "133121",
    "313121", "211331", "231131", "213113", "213311", "213131", "311123", "311321",
    "331121", "312113", "312311", "332111", "314111", "221411", "431111", "111224",
    "111422", "121124", "121421", "141122", "141221", "112214", "112412", "122114",
    "122411", "142112", "142211", "241211", "221114", "413111", "241112", "134111",
    "111242", "121142", "121241", "114212", "124112", "124211", "411212", "421112",
    "421211", "212141", "214121", "412121", "111143", "111341", "131141", "114113",
    "114311", "411113", "411311", "113141", "114131", "311141", "411131", "211412",
    "211214", "211232",
    "2331112",  # 106 = stop (7 widths)
]

_START_B = 104
_START_C = 105
_STOP = 106


def _code128_values(data: str):
    """Encode `data` into Code128 symbol values with auto B/C switching."""
    vals = []
    # Start in C if the whole string is an even-length run of >=4 digits, else B.
    i = 0
    n = len(data)

    def digit_run(pos):
        j = pos
        while j < n and data[j].isdigit():
            j += 1
        return j - pos

    use_c = digit_run(0) >= 4 and digit_run(0) % 2 == 0 and digit_run(0) == n
    if use_c:
        vals.append(_START_C)
        while i < n:
            vals.append(int(data[i:i + 2]))
            i += 2
    else:
        vals.append(_START_B)
        for ch in data:
            o = ord(ch)
            # Code128B covers ASCII 32..126 -> value = ord-32
            vals.append((o - 32) if 32 <= o <= 126 else (ord("?") - 32))
    # checksum: start value + sum(pos*value), mod 103
    checksum = vals[0]
    for pos, v in enumerate(vals[1:], start=1):
        checksum += pos * v
    vals.append(checksum % 103)
    vals.append(_STOP)
    return vals


def code128_svg(data: str, height: int = 48, module: int = 2, quiet: int = 10) -> str:
    """Render `data` as a Code128 barcode SVG (with human-readable caption)."""
    data = str(data or "").strip() or "0"
    values = _code128_values(data)
    x = quiet
    rects = []
    for v in values:
        pattern = _CODE128_PATTERNS[v]
        bar = True
        for w in pattern:
            width = int(w) * module
            if bar:
                rects.append(f"<rect x='{x}' y='0' width='{width}' height='{height}' fill='#000'/>")
            x += width
            bar = not bar
    total_w = x + quiet
    caption = (f"<text x='{total_w/2}' y='{height+14}' font-size='11' font-family='monospace' "
               f"text-anchor='middle' fill='#000'>{_xml_escape(data)}</text>")
    return (f"<svg xmlns='http://www.w3.org/2000/svg' width='{total_w}' height='{height+18}' "
            f"viewBox='0 0 {total_w} {height+18}' shape-rendering='crispEdges'>"
            f"<rect width='{total_w}' height='{height+18}' fill='#fff'/>{''.join(rects)}{caption}</svg>")


# ── QR ───────────────────────────────────────────────────────────────────────
def qr_svg(data: str, module: int = 3, quiet: int = 2) -> str:
    """Render `data` as a QR code SVG. Falls back to Code128 if segno is missing."""
    data = str(data or "").strip() or "0"
    try:
        import segno
    except Exception:
        return code128_svg(data)
    matrix = [list(row) for row in segno.make(data, error="m").matrix]
    size = len(matrix)
    dim = (size + quiet * 2) * module
    rects = []
    for r, row in enumerate(matrix):
        for c, cell in enumerate(row):
            if cell:
                x = (c + quiet) * module
                y = (r + quiet) * module
                rects.append(f"<rect x='{x}' y='{y}' width='{module}' height='{module}' fill='#000'/>")
    return (f"<svg xmlns='http://www.w3.org/2000/svg' width='{dim}' height='{dim}' "
            f"viewBox='0 0 {dim} {dim}' shape-rendering='crispEdges'>"
            f"<rect width='{dim}' height='{dim}' fill='#fff'/>{''.join(rects)}</svg>")


# ── EAN-13 / UPC-A ───────────────────────────────────────────────────────────
_EAN_L = ["0001101", "0011001", "0010011", "0111101", "0100011",
          "0110001", "0101111", "0111011", "0110111", "0001011"]
_EAN_R = ["".join("1" if b == "0" else "0" for b in c) for c in _EAN_L]
_EAN_G = [c[::-1] for c in _EAN_R]
# left-half parity by the (implicit) first digit: L = odd, G = even
_EAN_PARITY = ["LLLLLL", "LLGLGG", "LLGGLG", "LLGGGL", "LGLLGG",
               "LGGLLG", "LGGGLL", "LGLGLG", "LGLGGL", "LGGLGL"]

BARCODE_FIELDS = ("barcode", "sku", "code", "mpn")      # product fields a label may encode
# ii144b: every type in services/barcode_types.py (EAN/UPC/ITF/Code39/128/GS1-128/QR/Data Matrix/PDF417)
BARCODE_TYPES = ("code128", "ean13", "ean8", "upca", "upce", "itf14", "itf", "code39", "gs1_128", "qr",
                 "datamatrix", "pdf417")
_GTIN_LEN = {"ean13": 13, "upca": 12}


def gtin_check_digit(body: str) -> str:
    """GS1 mod-10 check digit: weights 3,1,3,… from the right of the body."""
    s = sum((3 if i % 2 == 0 else 1) * int(d) for i, d in enumerate(reversed(body)))
    return str((10 - s % 10) % 10)


def normalize_gtin(value: str, kind: str) -> str:
    """Return the full EAN-13 / UPC-A for `value`, or raise ValueError with a plain reason.
    A body one digit short gets its check digit appended; a full one is verified."""
    n = _GTIN_LEN[kind]
    name = "EAN-13" if kind == "ean13" else "UPC-A"
    v = str(value or "").strip().replace(" ", "").replace("-", "")
    if not v.isdigit():
        raise ValueError(f"{name} needs digits only")
    if len(v) == n - 1:
        return v + gtin_check_digit(v)
    if len(v) != n:
        raise ValueError(f"{name} needs {n - 1} or {n} digits")
    if gtin_check_digit(v[:-1]) != v[-1]:
        raise ValueError(f"{name} check digit is wrong (expected {gtin_check_digit(v[:-1])})")
    return v


def ean13_modules(code13: str) -> str:
    """The 95-module bar pattern ("1" = bar) for a validated 13-digit EAN."""
    first, left, right = int(code13[0]), code13[1:7], code13[7:]
    out = "101"
    for d, par in zip(left, _EAN_PARITY[first]):
        out += (_EAN_L if par == "L" else _EAN_G)[int(d)]
    out += "01010"
    for d in right:
        out += _EAN_R[int(d)]
    return out + "101"


def gtin_svg(value: str, kind: str = "ean13", height: int = 48, module: int = 2, quiet: int = 11) -> str:
    """EAN-13 or UPC-A as SVG (UPC-A is an EAN-13 with a leading 0). Raises ValueError
    when the value isn't a valid code of that kind."""
    code = normalize_gtin(value, kind)
    mods = ean13_modules(code if kind == "ean13" else "0" + code)
    guards = {0, 1, 2, 45, 46, 47, 48, 49, 92, 93, 94}
    rects = []
    for i, b in enumerate(mods):
        if b == "1":
            h = height + 6 if i in guards else height
            rects.append(f"<rect x='{quiet + i * module}' y='0' width='{module}' height='{h}' fill='#000'/>")
    total_w = quiet * 2 + len(mods) * module
    caption = (f"<text x='{total_w/2}' y='{height+18}' font-size='11' font-family='monospace' "
               f"text-anchor='middle' fill='#000'>{code}</text>")
    return (f"<svg xmlns='http://www.w3.org/2000/svg' width='{total_w}' height='{height+22}' "
            f"viewBox='0 0 {total_w} {height+22}' shape-rendering='crispEdges'>"
            f"<rect width='{total_w}' height='{height+22}' fill='#fff'/>{''.join(rects)}{caption}</svg>")


def barcode_svg(value: str, kind: str = "code128", height: int = 48) -> str:
    """One entry point for any supported symbology. Raises ValueError for a value the type can't
    hold (a wrong check digit, letters in an EAN, …)."""
    if kind == "qr" and not str(value or "").startswith("("):
        return qr_svg(value)
    if kind in ("ean13", "upca"):
        return gtin_svg(value, kind, height=height)
    if kind == "code128":
        return code128_svg(value, height=height)
    from backend.services import barcode_types
    return barcode_types.svg(kind, value, height=height)


def validate_barcode_options(field, types, default_type="code128") -> None:
    """Template-save check for the barcode source settings; raises ValueError (routers → 422)."""
    if field is not None and field not in BARCODE_FIELDS:
        raise ValueError(f"barcode_field must be one of {'|'.join(BARCODE_FIELDS)}")
    if default_type is not None and default_type not in BARCODE_TYPES:
        raise ValueError(f"barcode_type must be one of {'|'.join(BARCODE_TYPES)}")
    for f, t in (types or {}).items():
        if f not in BARCODE_FIELDS or t not in BARCODE_TYPES:
            raise ValueError(f"barcode_types: {f} → {t} isn't a supported field/type")


def unprintable_message(bad: list[str]) -> str:
    """One message for 'these products can't be encoded with this barcode type'."""
    more = f" and {len(bad) - 5} more" if len(bad) > 5 else ""
    return "These products can't be printed with this barcode type: " + "; ".join(bad[:5]) + more


def label_barcode(part, field: str | None, types: dict | None, default_type: str = "code128"):
    """What a label's barcode encodes for one product: (value, kind). `field` picks the product
    field (null = first filled of barcode/sku/code); `types` holds the symbology per field.
    The value is None when that field is empty on this product."""
    types = types if isinstance(types, dict) else {}
    if field in BARCODE_FIELDS:
        value = (getattr(part, field, None) or "").strip() or None
        return value, types.get(field) or default_type
    for f in ("barcode", "sku", "code"):
        value = (getattr(part, f, None) or "").strip()
        if value:
            return value, types.get(f) or default_type
    return None, default_type


def _xml_escape(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
