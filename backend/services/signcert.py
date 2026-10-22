"""144ig — signed-approval certificate PDF.

Renders a one-page, tamper-evident certificate for a DocumentSignature: who signed,
what they approved (doc type, number, total), when, from what IP/device, the drawn
signature image, and the SHA-256 hash of the document snapshot. In-house reportlab;
no third-party e-sign service. This is the artifact that holds up in a dispute where
Orderry's bare drawn scribble does not.
"""
import base64
import io
from datetime import datetime

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas


def build_certificate(sig, order, org) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    W, H = A4
    x = 20 * mm
    y = H - 25 * mm

    shop = (org.name if org else "") or "Tekly"
    c.setFont("Helvetica-Bold", 18)
    c.drawString(x, y, "Signed Approval Certificate")
    y -= 8 * mm
    c.setFont("Helvetica", 10)
    c.setFillColorRGB(0.4, 0.4, 0.4)
    c.drawString(x, y, f"{shop} · issued {datetime.utcnow():%Y-%m-%d %H:%M} UTC")
    c.setFillColorRGB(0, 0, 0)
    y -= 6 * mm
    c.line(x, y, W - 20 * mm, y)
    y -= 12 * mm

    doc_label = "Work order" if sig.doc_type == "work_order" else "Estimate"
    decision = (sig.decision or "accept").upper()
    total = f"{float(sig.doc_total or 0):,.2f} {sig.currency_code or ''}".strip()

    rows = [
        ("Document", f"{doc_label} {order.order_number if order else ''}".strip()),
        ("Decision", decision),
        ("Signed by", sig.signer_name or "—"),
        ("Signed at", f"{sig.signed_at:%Y-%m-%d %H:%M} UTC" if sig.signed_at else "—"),
        ("Amount", total or "—"),
        ("Signer IP", sig.signer_ip or "—"),
        ("Device", (sig.user_agent or "—")[:80]),
        ("Document hash (SHA-256)", sig.doc_hash or "—"),
    ]
    c.setFont("Helvetica", 11)
    for label, val in rows:
        c.setFillColorRGB(0.45, 0.45, 0.45)
        c.drawString(x, y, label)
        c.setFillColorRGB(0, 0, 0)
        c.drawString(x + 55 * mm, y, str(val))
        y -= 8 * mm

    # drawn signature image
    y -= 6 * mm
    c.setFillColorRGB(0.45, 0.45, 0.45)
    c.setFont("Helvetica", 10)
    c.drawString(x, y, "Signature")
    c.setFillColorRGB(0, 0, 0)
    if sig.signature_png and "," in sig.signature_png:
        try:
            raw = base64.b64decode(sig.signature_png.split(",", 1)[1])
            img = ImageReader(io.BytesIO(raw))
            c.drawImage(img, x, y - 32 * mm, width=70 * mm, height=28 * mm,
                        preserveAspectRatio=True, mask="auto")
        except Exception:
            c.drawString(x + 30 * mm, y, "(signature on file)")
    else:
        c.drawString(x + 30 * mm, y, "(no drawn signature captured)")

    y -= 42 * mm
    c.setFont("Helvetica-Oblique", 8)
    c.setFillColorRGB(0.5, 0.5, 0.5)
    c.drawString(x, y, "This certificate records an electronic approval. The document hash binds it to the exact")
    c.drawString(x, y - 4 * mm, "content the signer reviewed; any later change to that content will not match this hash.")

    c.showPage()
    c.save()
    return buf.getvalue()
