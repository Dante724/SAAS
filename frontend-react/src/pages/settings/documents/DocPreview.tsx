// The live document preview on Settings › Documents: the shop's own template filled in by the
// server with its latest real record, at the real paper width. Click a section for Hide / Move /
// Bigger / Smaller; click plain text to type over it. The frame never runs script: it is sandboxed
// without allow-scripts, and this page attaches the click handling from outside.
//
// Clicks map back by number, so every render remembers exactly which text it numbered (`source`).
// While a newer page is waiting for its render, clicks are ignored rather than applied to the
// wrong section, and an edit carries its source so the page can refuse a stale one.
import { useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ArrowDown, ArrowUp, Eye, EyeOff, Minus, Plus, X } from "lucide-react";
import { sanitizeHtml } from "../../../lib/safeHtml";
import { markForPreview, moveBlocked, parse, sections, isHidden, type EditOp } from "../templateEditor/simpleDoc";
import { chipLabel } from "../templateEditor/templateHtml";
import type { DocKind } from "../templateEditor/docFeatures";

export type { EditOp };
type Post = (path: string, body: unknown) => Promise<any>;

const LEFTOVER = /\{[A-Z][^{}<>]*\}/g;
/** Display only: a field the server had no value for shows as a grey sample, never as {Raw text}. */
export function samplesForLeftovers(html: string): string {
  if (!LEFTOVER.test(html)) return html;
  LEFTOVER.lastIndex = 0;
  const doc = new DOMParser().parseFromString(`<!doctype html><body>${html}</body>`, "text/html");
  const walker = doc.createTreeWalker(doc.body, NodeFilter.SHOW_TEXT);
  const texts: Text[] = [];
  for (let n = walker.nextNode(); n; n = walker.nextNode()) if (/\{[A-Z]/.test((n as Text).data) && (n.parentElement?.tagName !== "STYLE")) texts.push(n as Text);
  for (const t of texts) {
    const frag = doc.createDocumentFragment();
    let last = 0;
    t.data.replace(LEFTOVER, (m, at: number) => {
      frag.append(t.data.slice(last, at));
      const s = doc.createElement("span"); s.className = "tk-sample"; s.textContent = `Sample ${chipLabel(m).toLowerCase()}`;
      frag.append(s); last = at + m.length; return m;
    });
    frag.append(t.data.slice(last));
    t.replaceWith(frag);
  }
  return doc.body.innerHTML;
}

export default function DocPreview({ post, source, kind, page, font, refresh, onEdit, onSample }: {
  post: Post;
  /** the exact text edits apply to (editBase of the template); the preview numbers this */
  source: string;
  kind: DocKind; page: { w: number; h: number | null; pad: number }; font: string;
  /** bump to re-fetch (brand, logo or terms changed) */
  refresh: number;
  /** apply a click; a move returns where the section went, so the popover follows it */
  onEdit: (op: EditOp, source: string) => number | void;
  onSample?: (s: string | null) => void;
}) {
  const [rendered, setRendered] = useState<{ html: string; source: string } | null>(null);
  const [scale, setScale] = useState(1);
  const [height, setHeight] = useState(page.h || 420);
  const [pop, setPop] = useState<{ i: number; x: number; y: number } | null>(null);
  const box = useRef<HTMLDivElement | null>(null);
  const frame = useRef<HTMLIFrameElement | null>(null);
  const edit = useRef(onEdit); edit.current = onEdit;
  const current = useRef(source); current.current = source;
  const stale = !rendered || rendered.source !== source;
  const staleRef = useRef(stale); staleRef.current = stale;
  const secs = useMemo(() => sections(parse(source)), [source]);
  const count = secs.length;
  const hidden = pop && secs[pop.i] ? isHidden(secs[pop.i]) : false;
  const blocked = pop ? moveBlocked(source, pop.i) : null;

  // the real record's values, through the existing preview endpoint (marked so clicks map back)
  useEffect(() => {
    let on = true;
    const src = source;
    const t = setTimeout(() => {
      post("/documents/preview", { body_html: markForPreview(src), doc_type: kind })
        .then((r) => { if (on) { setRendered({ html: samplesForLeftovers(r?.html || ""), source: src }); onSample?.(r?.sample || null); } })
        .catch(() => { if (on) setRendered({ html: "<p style='color:#999'>Preview unavailable.</p>", source: src }); });
    }, 150);
    return () => { on = false; clearTimeout(t); };
  }, [source, kind, refresh]); // eslint-disable-line react-hooks/exhaustive-deps

  useLayoutEffect(() => {
    const el = box.current; if (!el) return;
    const fit = () => setScale(Math.min(1, (el.clientWidth - 48) / page.w));
    fit();
    const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(fit) : null;
    ro?.observe(el);
    return () => ro?.disconnect();
  }, [page.w]);

  function wire() {
    const doc = frame.current?.contentDocument; if (!doc) return;
    setHeight(Math.max(page.h || 0, doc.documentElement.scrollHeight));
    doc.addEventListener("click", (ev) => {
      if (staleRef.current) return;                       // a newer page is on its way: don't guess
      const t = ev.target as HTMLElement;
      const text = t.closest?.("[data-tk-e]") as HTMLElement | null;
      const secEl = t.closest?.("[data-tk-i]") as HTMLElement | null;
      doc.querySelectorAll(".tk-picked").forEach((n) => n.classList.remove("tk-picked"));
      if (secEl) {
        secEl.classList.add("tk-picked");
        const r = secEl.getBoundingClientRect();
        setPop({ i: Number(secEl.getAttribute("data-tk-i")), x: r.left, y: r.top });
      } else setPop(null);
      if (text && !text.isContentEditable) {
        const before = text.textContent || "";
        const src = current.current;
        text.contentEditable = "true"; text.focus();
        text.addEventListener("blur", () => {
          text.contentEditable = "false";
          const now = (text.textContent || "").trim();
          if (now && now !== before.trim()) edit.current({ type: "text", e: Number(text.getAttribute("data-tk-e")), text: now }, src);
        }, { once: true });
        text.addEventListener("keydown", (k) => { if (k.key === "Enter") { k.preventDefault(); text.blur(); } if (k.key === "Escape") { text.textContent = before; text.blur(); } });
      }
    });
  }

  const srcDoc = rendered == null ? "" : `<!doctype html><html><head><meta charset="utf-8"><style>
    html,body{margin:0;background:#fff}
    body{padding:${page.pad}px;width:${page.w}px;box-sizing:border-box;font-family:${font},Arial,sans-serif;color:#111;font-size:13px;line-height:1.5}
    table{border-collapse:collapse}img{max-width:100%}*{box-sizing:border-box}
    [data-tk-i]{cursor:pointer;transition:outline-color .1s;outline:1px dashed transparent;outline-offset:3px}
    [data-tk-i]:hover{outline-color:#93b4e6}
    .tk-picked{outline:2px solid #0059ba !important}
    [data-tk-e]:hover{background:rgba(0,89,186,.06)}
    [data-tk-e][contenteditable=true]{outline:2px solid #0059ba;background:#fff;cursor:text}
    [data-tk-hidden]{display:block !important;opacity:.32}
    table[data-tk-hidden]{display:table !important}
    .tk-sample{color:#64748b;font-style:italic}
  </style></head><body>${sanitizeHtml(rendered.html)}</body></html>`;

  const act = (op: EditOp) => {
    if (stale || !rendered) return;
    const moved = edit.current(op, rendered.source);
    if (op.type === "move") setPop((p) => (p && typeof moved === "number" && moved >= 0 ? { ...p, i: moved } : null));
  };
  const i = pop?.i ?? 0;

  return (
    <div ref={box} role="presentation" className="relative w-full overflow-hidden" onClick={(e) => { if (e.target === e.currentTarget) setPop(null); }}>
      <div className="mx-auto relative" style={{ width: page.w * scale, height: height * scale }}>
        {rendered == null && <div className="absolute inset-0 grid place-items-center text-xs text-ink-tertiary">Loading your document…</div>}
        <div className={`absolute top-0 left-0 origin-top-left bg-paper shadow-clay-drawer rounded-sm transition-opacity ${stale && rendered ? "opacity-70" : ""}`} style={{ width: page.w, height, transform: `scale(${scale})` }}>
          {rendered != null && (
            <iframe ref={frame} title="Document preview" sandbox="allow-same-origin" srcDoc={srcDoc} onLoad={wire}
              className={`block border-0 ${stale ? "pointer-events-none" : ""}`} style={{ width: page.w, height }} data-stale={stale ? "1" : "0"} />
          )}
          {page.h && height > page.h && <div aria-hidden="true" className="absolute left-0 right-0 border-t-2 border-dashed border-slate-300 pointer-events-none" style={{ top: page.h }} />}
        </div>
        {pop && !stale && (
          <div role="dialog" aria-label="Section" className="absolute z-20 bg-white border border-slate-200 rounded-xl shadow-drawer p-1"
            style={{ left: Math.max(0, pop.x * scale), top: Math.max(0, pop.y * scale - (blocked ? 66 : 46)) }}>
            <div className="flex items-center gap-0.5">
              <PopBtn label={hidden ? "Show" : "Hide"} onClick={() => act({ type: "hide", i, hidden: !hidden })}>{hidden ? <Eye size={14} /> : <EyeOff size={14} />}</PopBtn>
              <PopBtn label="Move up" disabled={i === 0 || !!blocked} onClick={() => act({ type: "move", i, dir: -1 })}><ArrowUp size={14} /></PopBtn>
              <PopBtn label="Move down" disabled={i >= count - 1 || !!blocked} onClick={() => act({ type: "move", i, dir: 1 })}><ArrowDown size={14} /></PopBtn>
              <PopBtn label="Smaller" onClick={() => act({ type: "size", i, step: -1 })}><Minus size={14} /></PopBtn>
              <PopBtn label="Bigger" onClick={() => act({ type: "size", i, step: 1 })}><Plus size={14} /></PopBtn>
              <button type="button" aria-label="Close" onClick={() => setPop(null)} className="w-7 h-7 rounded-lg grid place-items-center text-ink-tertiary hover:bg-slate-100"><X size={13} /></button>
            </div>
            {blocked && <p className="px-2 pb-1 text-2xs text-ink-tertiary max-w-xs">{blocked}</p>}
          </div>
        )}
      </div>
    </div>
  );
}

function PopBtn({ label, onClick, children, disabled }: { label: string; onClick: () => void; children: ReactNode; disabled?: boolean }) {
  return (
    <button type="button" onClick={onClick} disabled={disabled}
      className="h-8 px-2 rounded-lg inline-flex items-center gap-1 text-xs font-semibold text-ink-secondary hover:bg-slate-100 hover:text-ink-primary disabled:opacity-40">
      {children}{label}
    </button>
  );
}
