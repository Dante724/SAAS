"""Build 95 — printing.

Everything the shop physically hands over or sticks on a shelf: product labels and
price tags (with real scannable barcodes), and the stock documents — posting, write-off,
transfer, and the stocktake settlement act.

Barcodes are generated with reportlab's own Code128/QR, so there's no extra dependency
and no image round-trip: the barcode is drawn as vectors and stays crisp at any printer
resolution, which matters because a fuzzy barcode is a barcode that doesn't scan.
"""
import io
from datetime import datetime
from xml.sax.saxutils import escape as _x
from typing import Optional

from reportlab.graphics.barcode import code128
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as pdfcanvas
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

FIELDS = ["name", "sku", "mpn", "price", "barcode", "category", "brand"]


# ── labels & price tags ──────────────────────────────────────────────────────
def render_labels(items: list[dict], *, width_mm: float = 50, height_mm: float = 30,
                  fields: Optional[list[str]] = None, barcode_type: str = "code128",
                  copies: int = 1, org_name: str = "") -> bytes:
    """Lay labels out on A4, as many per sheet as the size allows.

    Each label is its own little canvas; anything that doesn't fit is simply not drawn
    rather than overflowing into its neighbour — a label that bleeds into the next one
    ruins the whole sheet.
    """
    fields = fields or ["name", "sku", "price", "barcode"]
    buf = io.BytesIO()
    c = pdfcanvas.Canvas(buf, pagesize=A4)
    page_w, page_h = A4

    lw, lh = width_mm * mm, height_mm * mm
    margin = 8 * mm
    cols = max(1, int((page_w - 2 * margin) // lw))
    rows = max(1, int((page_h - 2 * margin) // lh))
    per_page = cols * rows

    flat = []
    for it in items:
        flat.extend([it] * max(1, copies))

    for i, it in enumerate(flat):
        slot = i % per_page
        if i and slot == 0:
            c.showPage()
        col = slot % cols
        row = slot // cols
        x = margin + col * lw
        y = page_h - margin - (row + 1) * lh

        c.setStrokeColor(colors.HexColor("#DDDDDD"))
        c.rect(x, y, lw, lh, stroke=1, fill=0)

        cur = y + lh - 5 * mm
        pad = 3 * mm

        if "name" in fields:
            c.setFont("Helvetica-Bold", 8)
            name = (it.get("name") or "")[:28]
            c.drawString(x + pad, cur, name)
            cur -= 4 * mm
        if "brand" in fields and it.get("brand"):
            c.setFont("Helvetica", 6)
            c.drawString(x + pad, cur, str(it["brand"])[:30])
            cur -= 3 * mm
        if "sku" in fields and it.get("sku"):
            c.setFont("Helvetica", 6)
            c.drawString(x + pad, cur, f"SKU {it['sku']}")
            cur -= 3.5 * mm
        if "mpn" in fields and it.get("mpn"):
            c.setFont("Helvetica", 6)
            c.drawString(x + pad, cur, f"MPN {it['mpn']}")
            cur -= 3.5 * mm
        if "price" in fields and it.get("price") is not None:
            c.setFont("Helvetica-Bold", 11)
            c.drawString(x + pad, cur, f"{it['price']}")
            cur -= 5 * mm

        code = it.get("barcode")
        kind = it.get("barcode_kind") or barcode_type
        if "barcode" in fields and code:
            avail = cur - y - 2 * mm       # what's left below the text
            if avail > 6 * mm:
                if kind in ("ean13", "upca"):
                    # the caller has already validated/normalised the GTIN
                    from reportlab.graphics import renderPDF
                    from reportlab.graphics.barcode import eanbc
                    bh = min(avail, 9 * mm)
                    w = (eanbc.Ean13BarcodeWidget(code[:12]) if kind == "ean13"
                         else eanbc.UPCA(code[:11]))
                    w.barHeight = bh
                    bx0, by0, bx1, by1 = w.getBounds()
                    scale = min(1.0, (lw - 2 * pad) / max(1, bx1 - bx0))
                    d = Drawing((bx1 - bx0) * scale, (by1 - by0) * scale)
                    w.x, w.y = -bx0, -by0
                    d.add(w)
                    d.scale(scale, scale)
                    renderPDF.draw(d, c, x + pad, y + 2 * mm)
                elif kind == "qr":
                    from reportlab.graphics import renderPDF
                    from reportlab.graphics.barcode import qr
                    size = min(avail, lh * 0.4)
                    d = Drawing(size, size)
                    d.add(qr.QrCodeWidget(str(code), barWidth=size, barHeight=size))
                    renderPDF.draw(d, c, x + pad, y + 2 * mm)
                else:
                    bh = min(avail, 9 * mm)
                    bar = code128.Code128(str(code), barHeight=bh, barWidth=0.42 * mm / 1.2,
                                          humanReadable=True)
                    max_w = lw - 2 * pad
                    if bar.width > max_w:      # shrink until it fits the label
                        bar = code128.Code128(str(code), barHeight=bh,
                                              barWidth=(0.42 * mm / 1.2) * (max_w / bar.width),
                                              humanReadable=True)
                    bar.drawOn(c, x + pad, y + 2 * mm)

    c.showPage()
    c.save()
    return buf.getvalue()


# ── stock documents ──────────────────────────────────────────────────────────
def _styles():
    ss = getSampleStyleSheet()
    ss.add(ParagraphStyle(name="Small", parent=ss["Normal"], fontSize=8,
                          textColor=colors.HexColor("#666666")))
    return ss


def _doc_table(headers: list[str], rows: list[list], totals: Optional[list] = None) -> Table:
    data = [headers] + rows
    # totals: one row, or several (subtotal, a line per tax, total); the last one is bold.
    total_rows = totals if totals and isinstance(totals[0], (list, tuple)) else ([totals] if totals else [])
    data += [list(r) for r in total_rows]
    t = Table(data, repeatRows=1, hAlign="LEFT")
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F1F1F1")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#DDDDDD")),
        ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
    ]
    if total_rows:
        style += [("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                  ("BACKGROUND", (0, -len(total_rows)), (-1, -1), colors.HexColor("#FAFAFA"))]
    t.setStyle(TableStyle(style))
    return t


# Build 122: document vocabulary translations for the org's document language. Only the
# fixed labels are translated; user content (names, descriptions) is left as entered.
_DOC_PHRASES = {
    "Invoice":   {"hi": "चालान", "es": "Factura", "fr": "Facture", "de": "Rechnung", "pt": "Fatura", "ar": "فاتورة", "ru": "Счёт", "zh": "发票", "ja": "請求書"},
    "Estimate":  {"hi": "अनुमान", "es": "Presupuesto", "fr": "Devis", "de": "Kostenvoranschlag", "pt": "Orçamento", "ar": "تقدير", "ru": "Смета", "zh": "报价单", "ja": "見積書"},
    "Work Order":{"hi": "कार्य आदेश", "es": "Orden de trabajo", "fr": "Ordre de travail", "de": "Arbeitsauftrag", "pt": "Ordem de serviço", "ar": "أمر عمل", "ru": "Заказ-наряд", "zh": "工单", "ja": "作業指示書"},
    "Receipt":   {"hi": "रसीद", "es": "Recibo", "fr": "Reçu", "de": "Beleg", "pt": "Recibo", "ar": "إيصال", "ru": "Квитанция", "zh": "收据", "ja": "領収書"},
    "Item":      {"hi": "वस्तु", "es": "Artículo", "fr": "Article", "de": "Artikel", "pt": "Item", "ar": "بند", "ru": "Позиция", "zh": "项目", "ja": "項目"},
    "Description":{"hi": "विवरण", "es": "Descripción", "fr": "Description", "de": "Beschreibung", "pt": "Descrição", "ar": "الوصف", "ru": "Описание", "zh": "描述", "ja": "説明"},
    "Qty":       {"hi": "मात्रा", "es": "Cant.", "fr": "Qté", "de": "Menge", "pt": "Qtd", "ar": "الكمية", "ru": "Кол-во", "zh": "数量", "ja": "数量"},
    "Price":     {"hi": "मूल्य", "es": "Precio", "fr": "Prix", "de": "Preis", "pt": "Preço", "ar": "السعر", "ru": "Цена", "zh": "价格", "ja": "価格"},
    "Total":     {"hi": "कुल", "es": "Total", "fr": "Total", "de": "Gesamt", "pt": "Total", "ar": "المجموع", "ru": "Итого", "zh": "总计", "ja": "合計"},
    "Subtotal":  {"hi": "उप-योग", "es": "Subtotal", "fr": "Sous-total", "de": "Zwischensumme", "pt": "Subtotal", "ar": "المجموع الفرعي", "ru": "Подытог", "zh": "小计", "ja": "小計"},
    "Tax":       {"hi": "कर", "es": "Impuesto", "fr": "Taxe", "de": "Steuer", "pt": "Imposto", "ar": "ضريبة", "ru": "Налог", "zh": "税", "ja": "税"},
    "Credit note": {"hi": "क्रेडिट नोट", "es": "Nota de crédito", "fr": "Avoir", "de": "Gutschrift", "pt": "Nota de crédito", "ar": "إشعار دائن", "ru": "Кредит-нота", "zh": "贷项通知单", "ja": "クレジットノート"},
    "Amount":    {"hi": "राशि", "es": "Importe", "fr": "Montant", "de": "Betrag", "pt": "Valor", "ar": "المبلغ", "ru": "Сумма", "zh": "金额", "ja": "金額"},
}


def _t(phrase: str, lang: str) -> str:
    """Translate a fixed document label into ``lang``; unknown phrases pass through."""
    if not lang or lang == "en":
        return phrase
    return _DOC_PHRASES.get(phrase, {}).get(lang, phrase)


def render_document(*, title: str, org_name: str, number: Optional[str],
                    meta: list[tuple], headers: list[str], rows: list[list],
                    totals: Optional[list] = None, footer: Optional[str] = None,
                    lang: str = "en", header_lines: Optional[list[str]] = None,
                    tz: Optional[str] = None) -> bytes:
    """One layout for every stock document — they differ only in their columns, so
    giving each its own renderer would just be four ways to drift apart.

    ``lang`` translates the common document vocabulary (title words and column headers)
    into the org's document language so printed invoices/estimates read in that language.
    """
    title = _t(title, lang)
    headers = [_t(h, lang) for h in headers]
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=18 * mm, bottomMargin=18 * mm,
                            leftMargin=16 * mm, rightMargin=16 * mm, title=title)
    ss = _styles()
    story = []

    # Paragraph() parses its text as markup (<img src=...>, <link>, <font>...). Everything here
    # is data — supplier names, notes, references — so escape it to plain text.
    story.append(Paragraph(_x(org_name or ""), ss["Small"]))
    for line in header_lines or []:   # the issuer's address / contact lines
        story.append(Paragraph(_x(str(line)), ss["Small"]))
    story.append(Paragraph(_x(f"{title}{f' — {number}' if number else ''}"), ss["Title"]))
    story.append(Spacer(1, 4))

    if meta:
        mt = Table([[Paragraph(f"<b>{_x(str(k))}</b>", ss["Small"]), Paragraph(_x(str(v)), ss["Small"])]
                    for k, v in meta if v is not None], hAlign="LEFT", colWidths=[90, 320])
        mt.setStyle(TableStyle([("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                                ("TOPPADDING", (0, 0), (-1, -1), 2)]))
        story.append(mt)
        story.append(Spacer(1, 10))

    story.append(_doc_table(headers, rows, totals))

    if footer:
        story.append(Spacer(1, 14))
        story.append(Paragraph(_x(str(footer)), ss["Small"]))

    story.append(Spacer(1, 22))
    sig = Table([["Prepared by ______________________", "Approved by ______________________"]],
                hAlign="LEFT", colWidths=[230, 230])
    sig.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 8),
                             ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#666666"))]))
    story.append(sig)
    story.append(Spacer(1, 6))
    # On the shop's clock when we know it (launch B6), else UTC as before.
    generated, zone = datetime.utcnow(), "UTC"
    if tz and tz != "UTC":
        from backend.services.dates import utc_to_wall
        generated, zone = utc_to_wall(generated, tz), tz
    story.append(Paragraph(_x(f"Generated {generated:%Y-%m-%d %H:%M} {zone}"), ss["Small"]))

    doc.build(story)
    return buf.getvalue()
