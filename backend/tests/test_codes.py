"""Build 144v — real Code128 barcode + QR SVG generators."""
from backend.services.codes import _CODE128_PATTERNS, _code128_values, code128_svg, qr_svg


def test_code128_pattern_table_is_valid():
    assert len(_CODE128_PATTERNS) == 107
    for p in _CODE128_PATTERNS[:106]:
        assert len(p) == 6 and sum(int(x) for x in p) == 11
    assert _CODE128_PATTERNS[106] == "2331112"   # stop


def test_code128_checksum_and_start_selection():
    # alphanumeric -> Start B, checksum mod 103 correct, round-trips
    vals = _code128_values("WO-1234")
    assert vals[0] == 104 and vals[-1] == 106
    cs = vals[0] + sum(i * v for i, v in enumerate(vals[1:-2], start=1))
    assert cs % 103 == vals[-2]
    assert "".join(chr(v + 32) for v in vals[1:-2]) == "WO-1234"
    # long even digit run -> Start C
    assert _code128_values("0012345678")[0] == 105


def test_code128_svg_shape():
    svg = code128_svg("ABC-99")
    assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>")
    assert "<rect" in svg and "ABC-99" in svg     # bars + human-readable caption
    assert "shape-rendering='crispEdges'" in svg


def test_qr_svg_renders_matrix():
    svg = qr_svg("https://shop.example/o/WO-1234")
    assert svg.startswith("<svg") and svg.count("<rect") > 20   # QR modules
    # different data => different module layout
    assert qr_svg("A") != qr_svg("B")


def test_xml_safe_data():
    # a data value with XML-special chars must not break the SVG caption
    svg = code128_svg("A&B<C")
    assert "&amp;" in svg and "&lt;" in svg and "<rect" in svg
