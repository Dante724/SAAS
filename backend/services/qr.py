"""QR code generation (pure-Python, no native deps)."""
import base64
import io

import segno


def qr_svg_data_uri(data: str, scale: int = 5) -> str:
    """Return a QR code for `data` as an SVG data URI (embeddable in <img src>)."""
    buf = io.BytesIO()
    segno.make(data, error="m").save(buf, kind="svg", scale=scale, border=2)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/svg+xml;base64,{b64}"
