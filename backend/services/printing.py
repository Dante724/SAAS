"""Build 95 — printing.

The stock documents the shop hands over — write-off, transfer, return, purchase order and
the stocktake settlement act. (ii144c: labels and price tags print from label templates as HTML —
routers/documents.py.)

Barcodes are generated with reportlab's own Code128/QR, so there's no extra dependency
and no image round-trip: the barcode is drawn as vectors and stays crisp at any printer
resolution, which matters because a fuzzy barcode is a barcode that doesn't scan.
"""
import io
from datetime import datetime
from xml.sax.saxutils import escape as _x
from typing import Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# Fields a label template may show (validated by routers/printing.py templates).
FIELDS = ["name", "sku", "mpn", "price", "barcode", "category", "brand"]


# ── stock documents ──────────────────────────────────────────────────────────
def _styles():
    ss = getSampleStyleSheet()
    ss.add(ParagraphStyle(name="Small", parent=ss["Normal"], fontSize=8,
                          textColor=colors.HexColor("#666666")))
    return ss


def _doc_table(headers: list[str], rows: list[list], totals: Optional[list] = None) -> Table:
    data = [headers] + rows
    if totals:
        data.append(totals)
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
    if totals:
        style += [("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                  ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#FAFAFA"))]
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
                    lang: str = "en") -> bytes:
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
    story.append(Paragraph(
        f"Generated {datetime.utcnow():%Y-%m-%d %H:%M} UTC", ss["Small"]))

    doc.build(story)
    return buf.getvalue()
