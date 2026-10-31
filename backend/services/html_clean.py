"""Server-side cleaning for HTML the shop writes itself (document and message templates).

The browser already cleans before showing a template (frontend-react/src/lib/safeHtml.ts),
but anything else that renders a stored template — a print view, a PDF, an email client,
an older bundle — would run whatever was saved (H5). nh3 (Rust "ammonia") removes script,
event handlers and javascript: URLs while keeping what a printable template needs: layout
tags, tables, images, inline styles and <style> blocks. Placeholders ({{client_name}},
{#if paid}) are plain text, so they pass through untouched.
"""
import re

import nh3

_TAGS = {
    "a", "abbr", "address", "article", "aside", "b", "bdi", "bdo", "big", "blockquote", "br",
    "caption", "center", "cite", "code", "col", "colgroup", "dd", "del", "details", "dfn", "div",
    "dl", "dt", "em", "figcaption", "figure", "font", "footer", "h1", "h2", "h3", "h4", "h5",
    "h6", "header", "hgroup", "hr", "i", "img", "ins", "kbd", "li", "main", "mark", "nav", "ol",
    "p", "pre", "q", "rp", "rt", "ruby", "s", "samp", "section", "small", "span", "strike",
    "strong", "style", "sub", "summary", "sup", "table", "tbody", "td", "tfoot", "th", "thead",
    "time", "tr", "tt", "u", "ul", "var", "wbr",
}
_GENERIC_ATTRS = {
    "style", "class", "id", "title", "align", "valign", "width", "height", "dir", "lang",
    "bgcolor", "color", "border", "colspan", "rowspan", "cellpadding", "cellspacing",
    "face", "size", "role", "aria-label", "aria-hidden",
}
_ATTRS = {"*": _GENERIC_ATTRS, "a": {"href", "target", "name"}, "img": {"src", "alt"},
          "ol": {"start", "type"}, "td": {"nowrap"}, "th": {"nowrap", "scope"}, "time": {"datetime"}}
# CSS that can run script in old engines; nh3 doesn't parse CSS, so strip these by hand.
_BAD_CSS = ("expression(", "javascript:", "behavior:", "-moz-binding")


def _attr_filter(tag: str, attr: str, value: str):
    if attr == "style" and any(b in value.lower().replace(" ", "") for b in _BAD_CSS):
        return None
    if tag == "img" and attr == "src":
        v = value.strip().lower()
        if v.startswith("data:") and not v.startswith(("data:image/png", "data:image/jpeg",
                                                      "data:image/jpg", "data:image/gif", "data:image/webp")):
            return None
    return value


# Template markers in text ({{client_name}}, {#if paid}, {/if}, {:else}). Between table rows an
# HTML parser would move plain text out of the table, breaking a row loop or condition, so
# they ride through the cleaner as comments (which stay in place) and are put back after.
_MARKER = re.compile(r"\{\{[^{}<>]*\}\}|\{[#/:][^{}<>]*\}")
_TAG_OR_TEXT = re.compile(r"(<[^>]*>)")
_SLOT = re.compile(r"<!--tpl(\d+)-->")


def _protect(html: str) -> tuple[str, list[str]]:
    saved: list[str] = []

    def keep(m):
        saved.append(m.group(0))
        return f"<!--tpl{len(saved) - 1}-->"
    parts = _TAG_OR_TEXT.split(html)
    return "".join(p if p.startswith("<") else _MARKER.sub(keep, p) for p in parts), saved


def clean_template_html(html: str | None) -> str:
    if not html:
        return html or ""
    html, saved = _protect(html)
    out = nh3.clean(
        html, tags=_TAGS, attributes=_ATTRS, clean_content_tags={"script"},
        url_schemes={"http", "https", "mailto", "tel", "data"}, link_rel=None,
        strip_comments=False, attribute_filter=_attr_filter, generic_attribute_prefixes={"data-"})
    out = _SLOT.sub(lambda m: saved[int(m.group(1))] if int(m.group(1)) < len(saved) else "", out)
    # a <style> block can still smuggle script in very old engines
    low = out.lower()
    if "<style" in low and any(b in low for b in _BAD_CSS):
        out = re.sub(r"(?is)<style\b[^>]*>.*?</style>",
                     lambda m: "" if any(b in m.group(0).lower() for b in _BAD_CSS) else m.group(0), out)
    return out
