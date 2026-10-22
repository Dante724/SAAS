// HTML that came from the server or a user (document/email templates, rendered previews) must
// never run script in this origin — the session token lives in localStorage. These helpers are
// used wherever such HTML is put into the live page or a print frame.

// Elements that can execute or load active content.
const DROP = new Set([
  "SCRIPT", "IFRAME", "FRAME", "FRAMESET", "OBJECT", "EMBED", "APPLET", "BASE", "LINK", "META",
  "FORM", "NOSCRIPT", "TEMPLATE", "FOREIGNOBJECT", "SET", "ANIMATE", "ANIMATETRANSFORM", "ANIMATEMOTION",
]);
const URL_ATTRS = new Set(["href", "src", "xlink:href", "action", "formaction", "background", "poster", "srcset", "data", "lowsrc", "dynsrc"]);
// Inline images (logos, QR codes) are the only data: URLs templates legitimately use.
const BAD_URL = /^(javascript|vbscript|livescript):|^data:(?!image\/(png|jpe?g|gif|webp);)/;

/** Remove script-capable elements, event-handler attributes and script URLs from an HTML
 *  fragment. Parsing happens in an inert document, so nothing executes while sanitising. */
export function sanitizeHtml(html: string): string {
  const doc = new DOMParser().parseFromString(`<!doctype html><body>${html || ""}</body>`, "text/html");
  const clean = (el: Element) => {
    for (const child of Array.from(el.children)) {
      if (DROP.has(child.tagName.toUpperCase())) { child.remove(); continue; }
      for (const a of Array.from(child.attributes)) {
        const name = a.name.toLowerCase();
        const val = a.value.replace(/[\s\u0000-\u001f]+/g, "").toLowerCase();
        if (name.startsWith("on") || val.startsWith("javascript:") ||
            (URL_ATTRS.has(name) && BAD_URL.test(val)) ||
            (name === "style" && /expression\(|javascript:|url\(\s*['"]?\s*javascript:/.test(val))) {
          child.removeAttribute(a.name);
        }
      }
      clean(child);
    }
  };
  clean(doc.body);
  return doc.body.innerHTML;
}

export function escapeHtml(s: unknown): string {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c] as string));
}

/** Print server-rendered HTML through a hidden frame that cannot run script (no
 *  `allow-scripts`) but still lets this page call print() on it. */
export function printHtmlInFrame(html: string): void {
  const ifr = document.createElement("iframe");
  ifr.setAttribute("sandbox", "allow-same-origin allow-modals");
  ifr.style.cssText = "position:fixed;right:0;bottom:0;width:0;height:0;border:0;visibility:hidden";
  ifr.srcdoc = html;
  ifr.onload = () => { try { ifr.contentWindow?.focus(); ifr.contentWindow?.print(); } catch { /* noop */ } setTimeout(() => ifr.remove(), 2000); };
  document.body.appendChild(ifr);
}
