"""Barcode & QR rendering as self-contained inline SVG.

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


def _xml_escape(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
