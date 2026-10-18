"""Workflow-native AI seam (Phase 3B).

Three operations that sit inside the repair workflow: summarize an order's
activity, draft estimate line items from a customer complaint, and suggest a
chat reply. When ANTHROPIC_API_KEY is set the real model is called; otherwise a
deterministic stub returns well-formed output so the feature is testable and the
UI works in every environment. Provider failures degrade to the stub, never raise.
"""
import json
from typing import Optional

from backend.config import settings


def is_live() -> bool:
    return bool(settings.ANTHROPIC_API_KEY)


def provider() -> str:
    return "anthropic" if is_live() else "stub"


async def _complete(system: str, user: str, max_tokens: int = 700) -> Optional[str]:
    """Call the Anthropic Messages API. Returns text or None on failure/no-key."""
    if not is_live():
        return None
    try:
        import httpx
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": settings.ANTHROPIC_API_KEY,
                         "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": settings.AI_MODEL, "max_tokens": max_tokens,
                      "system": system, "messages": [{"role": "user", "content": user}]})
        if resp.status_code != 200:
            return None
        blocks = resp.json().get("content", [])
        return "".join(b.get("text", "") for b in blocks if b.get("type") == "text") or None
    except Exception:
        return None


async def _complete_vision(system: str, user: str, image_b64: str, media_type: str,
                           max_tokens: int = 1500) -> Optional[str]:
    """Call Anthropic Messages with an image + text block. Returns text or None on failure/no-key."""
    if not is_live():
        return None
    try:
        import httpx
        content = [
            {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_b64}},
            {"type": "text", "text": user},
        ]
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": settings.ANTHROPIC_API_KEY,
                         "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={"model": settings.AI_MODEL, "max_tokens": max_tokens,
                      "system": system, "messages": [{"role": "user", "content": content}]})
        if resp.status_code != 200:
            return None
        blocks = resp.json().get("content", [])
        return "".join(b.get("text", "") for b in blocks if b.get("type") == "text") or None
    except Exception:
        return None


def _stub_posting_rows(text: str) -> list[dict]:
    """Deterministic fallback: parse pasted lines into posting rows when no model is available.
    Columns are read positionally (Code, Name, Qty, Cost) from tab/comma/2+space separators;
    a leading 'N x Name' is also understood."""
    import re
    rows: list[dict] = []
    for raw in (text or "").splitlines():
        s = raw.strip(" -•\t")
        if not s:
            continue
        m = re.match(r"^(\d+)\s*[x×*]\s*(.+)$", s, re.I)
        if m:
            rows.append({"code": "", "name": m.group(2).strip(), "qty": float(m.group(1)), "purchase_price": 0.0})
            continue
        cells = [c.strip() for c in re.split(r"\t|,|\s{2,}", s) if c.strip()]
        if not cells:
            continue
        # trailing numerics → cost then qty; leading token → code; remainder → name
        nums = [c for c in cells if re.fullmatch(r"\d+(\.\d+)?", c)]
        qty = float(nums[0]) if len(nums) >= 1 else 1.0
        cost = float(nums[1]) if len(nums) >= 2 else 0.0
        words = [c for c in cells if c not in nums]
        code = words[0] if words else ""
        name = " ".join(words[1:]) if len(words) > 1 else ""
        rows.append({"code": code, "name": name, "qty": qty, "purchase_price": cost})
    return rows[:200]


async def extract_posting_rows(*, text: Optional[str] = None, image_b64: Optional[str] = None,
                               media_type: Optional[str] = None) -> dict:
    """Turn a supplier invoice / packing-slip photo OR a pasted list into posting rows.
    Output is ALWAYS routed through the posting preview/confirm gate by the caller — it never
    writes stock directly. Live vision/text model when a key is set; deterministic text stub
    otherwise (a photo with no model configured returns an explanatory, empty result)."""
    sys = ("You extract stock-receiving lines from a supplier invoice, packing slip, or a pasted "
           'parts list. Return ONLY JSON: {"rows":[{"code":str,"name":str,"qty":number,'
           '"purchase_price":number}]}. code = SKU/part number if shown else ""; name = the '
           "product description; qty = quantity received; purchase_price = unit cost if shown else 0. "
           "Ignore totals, tax and shipping lines. No prose.")
    out = None
    if image_b64:
        out = await _complete_vision(sys, "Extract the receiving lines from this document.",
                                     image_b64, media_type or "image/jpeg")
    elif text:
        out = await _complete(sys, text, 1500)
    if out:
        try:
            data = json.loads(out[out.find("{"):out.rfind("}") + 1])
            rows = [{"code": str(r.get("code", "") or "").strip(),
                     "name": str(r.get("name", "") or "").strip(),
                     "qty": float(r.get("qty", 1) or 1),
                     "purchase_price": float(r.get("purchase_price", 0) or 0)}
                    for r in data.get("rows", []) if (str(r.get("code", "")).strip() or str(r.get("name", "")).strip())]
            if rows:
                return {"provider": provider(), "rows": rows[:200]}
        except Exception:
            pass
    if image_b64 and not is_live():
        return {"provider": "stub", "rows": [],
                "note": "Photo extraction needs an AI provider configured. Paste the rows or type them instead."}
    return {"provider": "stub", "rows": _stub_posting_rows(text or "")}


# ── operations ───────────────────────────────────────────────────────────────
async def summarize_order(*, order_number: str, complaint: str, status: str,
                          events: list[str]) -> dict:
    sys = ("You are a service-desk assistant. Summarize a repair order's status in "
           "2-3 sentences for a busy technician. Be concrete and neutral.")
    usr = (f"Order {order_number}\nStatus: {status}\nComplaint: {complaint}\n"
           f"Recent activity:\n- " + "\n- ".join(events[-12:] or ["(no activity yet)"]))
    text = await _complete(sys, usr, 400)
    if text:
        return {"provider": "anthropic", "summary": text.strip()}
    # deterministic stub
    last = events[-1] if events else "no activity yet"
    summary = (f"Order {order_number} is currently '{status}'. "
               f"Reported issue: {complaint or 'n/a'}. "
               f"Most recent activity: {last}. {len(events)} event(s) on file.")
    return {"provider": "stub", "summary": summary}


async def draft_estimate_lines(*, complaint: str, device: str = "") -> dict:
    sys = ("You draft repair estimate line items. Return ONLY JSON: a list of objects "
           "with keys description (string), qty (number), unit_price (number). 1-5 lines.")
    usr = f"Device: {device or 'unspecified'}\nCustomer complaint: {complaint}"
    text = await _complete(sys, usr, 600)
    if text:
        try:
            cleaned = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            lines = json.loads(cleaned)
            norm = [{"description": str(l.get("description", "")).strip(),
                     "qty": float(l.get("qty", 1) or 1),
                     "unit_price": float(l.get("unit_price", 0) or 0)} for l in lines][:5]
            if norm:
                return {"provider": "anthropic", "lines": norm}
        except Exception:
            pass
    # deterministic stub: a sensible diagnostic + labor scaffold
    c = (complaint or "").lower()
    lines = [{"description": "Diagnostic / inspection", "qty": 1, "unit_price": 35.0},
             {"description": "Bench labor (1h)", "qty": 1, "unit_price": 60.0}]
    if "screen" in c or "crack" in c or "display" in c:
        lines.insert(1, {"description": "Display assembly replacement", "qty": 1, "unit_price": 120.0})
    elif "battery" in c:
        lines.insert(1, {"description": "Battery replacement", "qty": 1, "unit_price": 45.0})
    elif "water" in c or "liquid" in c:
        lines.insert(1, {"description": "Liquid-damage cleaning + reball", "qty": 1, "unit_price": 90.0})
    return {"provider": "stub", "lines": lines}


async def suggest_reply(*, customer_message: str, context: str = "") -> dict:
    sys = ("You write concise, friendly customer-service replies for a repair shop. "
           "One short paragraph, no greeting line needed.")
    usr = f"Context: {context}\nCustomer wrote: {customer_message}"
    text = await _complete(sys, usr, 300)
    if text:
        return {"provider": "anthropic", "reply": text.strip()}
    reply = ("Thanks for reaching out. We've received your message and a technician "
             "will review your repair shortly. We'll follow up with an update as soon "
             "as we have one. Is there anything else you'd like us to look at?")
    return {"provider": "stub", "reply": reply}


async def draft_product_description(*, name: str, brand: str = "", category: str = "",
                                    specs: str = "") -> dict:
    """A5: generate a catalog product description. Live model or deterministic stub."""
    sys = ("You are a concise product copywriter for a repair shop catalog. "
           "Write a clear 2-3 sentence description. No marketing fluff.")
    usr = f"Name: {name}\nBrand: {brand}\nCategory: {category}\nSpecs: {specs}"
    out = await _complete(sys, usr, max_tokens=300)
    if out:
        return {"provider": provider(), "description": out.strip()}
    bits = [b for b in [brand, name] if b]
    lead = " ".join(bits) or name
    cat = f" {category.lower()}" if category else ""
    desc = (f"{lead} is a{cat} item stocked for repair and resale. "
            f"{('Specifications: ' + specs + '. ') if specs else ''}"
            "Contact us to confirm compatibility for your job.")
    return {"provider": "stub", "description": desc}


async def recognize_items(*, text: str) -> dict:
    """A5: extract structured line-items (part/service candidates) from free text
    (e.g. a pasted parts list or a dictated job). Returns a list of {name, qty}."""
    sys = ("Extract repair line items from the text. Return ONLY JSON: "
           '{"items":[{"name":str,"qty":number}]}. No prose.')
    out = await _complete(sys, text, max_tokens=500)
    if out:
        try:
            data = json.loads(out[out.find("{"):out.rfind("}") + 1])
            items = [{"name": str(i.get("name", "")).strip(), "qty": float(i.get("qty", 1) or 1)}
                     for i in data.get("items", []) if str(i.get("name", "")).strip()]
            if items:
                return {"provider": provider(), "items": items}
        except Exception:
            pass
    # deterministic stub: one line per non-empty text line, qty parsed from a leading "N x"
    import re
    items = []
    for raw in text.splitlines():
        s = raw.strip(" -•\t")
        if not s:
            continue
        m = re.match(r"^(\d+)\s*[x×*]\s*(.+)$", s, re.I)
        if m:
            items.append({"name": m.group(2).strip(), "qty": float(m.group(1))})
        else:
            items.append({"name": s, "qty": 1.0})
    return {"provider": "stub", "items": items[:50]}


def _doc_section(key: str, label: str, inner: str) -> str:
    return f'<section data-block="{key}" data-label="{label}">{inner}</section>'


# plain-{token} section snippets — mirror the editor's Blocks library. The FE
# turns {Tokens} into pills on load, so the stub output drops straight onto the canvas.
_DOC_BLOCKS = {
    "header": ("Header", '<div style="display:flex;justify-content:space-between;align-items:flex-start">{Company Logo}<div style="text-align:right"><h1 style="margin:0">{Company Name}</h1><div>{Company Address}</div></div></div>'),
    "invoice_header": ("Header", '<div style="display:flex;justify-content:space-between;align-items:flex-start">{Company Logo}<div style="text-align:right"><h1 style="margin:0">Invoice</h1><div>{Invoice Number}</div><div>Due {Invoice Due Date}</div></div></div>'),
    "client": ("Client", '<p><strong>For:</strong> {Client Name}<br/>{Client Phone} · {Client Email}<br/>{Client Address}</p>'),
    "device": ("Device / asset", '<p><strong>Device:</strong> {Asset Brand} {Asset Model} · SN {Asset Serial Number}</p>'),
    # Forms → Customize → "Shows on: Printed receipt" decides which fields this lists.
    "custom_fields": ("Form details", '<div>{Custom Fields}</div>'),
    "items": ("Line items", '<h3>Work</h3><table style="width:100%;border-collapse:collapse;margin:8px 0"><thead><tr><th style="border:1px solid #ddd;padding:6px;text-align:left;background:#f5f5f5">#</th><th style="border:1px solid #ddd;padding:6px;text-align:left;background:#f5f5f5">Item</th><th style="border:1px solid #ddd;padding:6px;text-align:left;background:#f5f5f5">Qty</th><th style="border:1px solid #ddd;padding:6px;text-align:left;background:#f5f5f5">Price</th><th style="border:1px solid #ddd;padding:6px;text-align:left;background:#f5f5f5">Amount</th></tr></thead><tbody><tr><td style="border:1px solid #ddd;padding:6px">{Row Number}</td><td style="border:1px solid #ddd;padding:6px">{Product Name}</td><td style="border:1px solid #ddd;padding:6px">{Product Qty}</td><td style="border:1px solid #ddd;padding:6px">{Product Price}</td><td style="border:1px solid #ddd;padding:6px">{Product Amount}</td></tr></tbody></table>'),
    "totals": ("Totals", '<p style="text-align:right">Subtotal {Subtotal}<br/>Tax {Tax Value}<br/><strong>Total {Order Total}</strong><br/>Balance due {Order Balance Due}</p>'),
    "deposit": ("Deposit", '<p style="text-align:right">Deposit required: <strong>{Order Prepayment}</strong></p>'),
    "summary": ("Work summary", '<p style="font-size:12px;color:#555">{Work Summary}</p>'),
    "signature": ("Signature", '<div style="margin-top:24px"><div style="border-bottom:1px solid #333;width:260px;height:28px"></div><div style="font-size:11px;color:#555;margin-top:3px">Signature / Date</div></div>'),
    "terms": ("Terms & notes", '<hr/><p style="font-size:11px;color:#666">Terms: all repairs carry a 90-day warranty on parts and labour unless stated otherwise.</p>'),
}


# Named presets — one-click ready layouts. Purely local; the owner can pick one or
# describe their own and the same in-house engine assembles the blocks.
DOC_PRESETS: dict[str, dict] = {
    "estimate_standard": {"label": "Standard estimate", "doc_type": "estimate",
                          "blocks": ["header", "client", "device", "custom_fields", "items", "totals", "summary", "signature"]},
    "invoice_detailed": {"label": "Detailed invoice", "doc_type": "invoice",
                         "blocks": ["invoice_header", "client", "items", "totals", "terms"]},
    "receipt_simple": {"label": "Simple receipt", "doc_type": "receipt",
                       "blocks": ["header", "client", "items", "totals"]},
    "warranty_slip": {"label": "Warranty slip", "doc_type": "work_order",
                      "blocks": ["header", "client", "device", "custom_fields", "terms", "signature"]},
    "work_order_full": {"label": "Full work order", "doc_type": "work_order",
                        "blocks": ["header", "client", "device", "custom_fields", "items", "totals", "summary", "signature", "terms"]},
}


def _assemble(keys: list[str]) -> str:
    seen, body = set(), []
    for k in keys:
        if k in seen or k not in _DOC_BLOCKS:
            continue
        seen.add(k)
        label, inner = _DOC_BLOCKS[k]
        body.append(_doc_section("header" if k == "invoice_header" else k, label, inner))
    return "".join(body)


def generate_document_template(*, prompt: str = "", doc_type: str = "estimate", preset: str = "") -> dict:
    """Tekly's OWN document intelligence — fully in-house, no third-party API. Either
    expands a named preset, or reads the plain-English description and maps it to the
    block library by keyword. Deterministic and offline by design."""
    if preset and preset in DOC_PRESETS:
        p = DOC_PRESETS[preset]
        return {"provider": "builtin", "preset": preset, "body_html": _assemble(p["blocks"])}

    p = (prompt or "").lower()
    invoice = doc_type == "invoice" or "invoice" in p
    keys = ["invoice_header" if invoice else "header", "client"]
    if any(w in p for w in ("device", "asset", "vehicle", "serial", "imei", "vin", "phone", "laptop")):
        keys.append("device")
    if any(w in p for w in ("custom field", "intake details", "form details", "passcode", "accessor")):
        keys.append("custom_fields")
    keys.append("items")
    if any(w in p for w in ("deposit", "prepay", "down payment", "upfront")):
        keys.append("deposit")
    keys.append("totals")
    if any(w in p for w in ("summary", "work performed", "diagnos", "notes")):
        keys.append("summary")
    if any(w in p for w in ("sign", "signature", "authoriz", "approve")):
        keys.append("signature")
    if any(w in p for w in ("term", "warranty", "condition", "policy", "legal")):
        keys.append("terms")
    return {"provider": "builtin", "body_html": _assemble(keys)}


async def transcribe_audio(*, audio_present: bool, hint: str = "") -> dict:
    """A5: transcription seam. Real speech-to-text requires a provider + audio
    upload pipeline (not wired in-sandbox); returns a gated, honest response so
    the UI can integrate against a stable contract."""
    if not audio_present:
        return {"provider": "stub", "available": False, "text": "",
                "note": "No audio supplied."}
    # Live transcription would POST the audio to a STT provider here.
    return {"provider": "stub", "available": is_live(),
            "text": (hint or "").strip(),
            "note": ("Transcription runs when a speech provider is configured; "
                     "returning the supplied hint as a placeholder.")}


async def remove_background(*, image_present: bool) -> dict:
    """A5: background-removal seam for product photos. Real processing needs an
    image pipeline/provider; returns a gated contract response in-sandbox."""
    if not image_present:
        return {"provider": "stub", "processed": False, "note": "No image supplied."}
    return {"provider": "stub", "processed": is_live(),
            "note": ("Background removal runs when an image provider is configured. "
                     "Original image is returned unchanged in this environment.")}
