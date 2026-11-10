// "Start from a picture": ready-made layouts shown as real pages filled in with the shop's own
// logo, name and a recent job. Picking one creates the template through the existing API and
// opens it for editing. The layouts are Tekly's own Smart builder presets and the templates every
// shop is seeded with, so nothing new is stored.
import { useEffect, useState } from "react";
import { FilePlus2, Sparkles, X } from "lucide-react";
import { buttonClass } from "../../../components/Button";
import { proposeFix } from "./autoFix";
import { staticReasons } from "./templateHtml";

type Api = { get: (p: string) => Promise<any>; post: (p: string, b?: unknown) => Promise<any> };
export type Layout = { key: string; label: string; hint: string; doc_type: string; page_size: string; page_w_mm?: number | null; page_h_mm?: number | null; body_html: string };

type Spec = { key: string; label: string; hint: string; doc_type: string; page_size: string; preset?: string; seeded?: string };
const SPECS: Spec[] = [
  { key: "invoice", label: "Invoice", hint: "A4 · logo, items and totals", doc_type: "invoice", page_size: "a4", preset: "invoice_detailed" },
  { key: "receipt", label: "Receipt, 80 mm", hint: "Till roll · sales and payments", doc_type: "receipt", page_size: "receipt80", seeded: "Sales receipt", preset: "receipt_simple" },
  { key: "estimate", label: "Estimate / quote", hint: "A4 · sent for approval", doc_type: "estimate", page_size: "a4", preset: "estimate_standard" },
  { key: "work_order", label: "Work order", hint: "A4 · given at check-in", doc_type: "work_order", page_size: "a4", preset: "work_order_full" },
  { key: "label", label: "Price label", hint: "Sticker for items and shelves", doc_type: "label", page_size: "label", seeded: "Price tag" },
];

/** The layouts on offer, built from the presets and this shop's seeded templates. A seeded layout
 *  that would open greyed out gets the same safe rewrite the editor offers, so it opens in Design. */
export async function loadLayouts(api: Api, known: any[] = []): Promise<Layout[]> {
  const templates: any[] = known.length ? known : ((await api.get("/documents/templates").catch(() => null))?.items || []);
  const out: Layout[] = [];
  for (const s of SPECS) {
    const seeded = s.seeded ? templates.find((t) => (t.name || "").toLowerCase() === s.seeded!.toLowerCase() && t.body_html) : null;
    let body = seeded?.body_html as string | undefined;
    if (body && staticReasons(body).design) body = proposeFix(body)?.html ?? body;
    if (!body && s.preset) body = (await api.post("/documents/ai-generate", { prompt: "", doc_type: s.doc_type, preset: s.preset }).catch(() => null))?.body_html;
    if (!body) continue;
    out.push({ key: s.key, label: s.label, hint: s.hint, doc_type: s.doc_type, page_size: seeded?.page_size || s.page_size,
      page_w_mm: seeded?.page_w_mm ?? (s.doc_type === "label" ? 50 : null), page_h_mm: seeded?.page_h_mm ?? (s.doc_type === "label" ? 30 : null), body_html: body });
  }
  return out;
}

/** A name not used yet: "Invoice", then "Invoice 2", … */
export function freeName(base: string, templates: any[]): string {
  const used = new Set(templates.map((t) => (t.name || "").toLowerCase()));
  if (!used.has(base.toLowerCase())) return base;
  for (let i = 2; ; i++) if (!used.has(`${base} ${i}`.toLowerCase())) return `${base} ${i}`;
}

export async function createFromLayout(api: Api, l: Layout, templates: any[]): Promise<any> {
  const row = { name: freeName(l.label, templates), doc_type: l.doc_type, page_size: l.page_size, page_w_mm: l.page_w_mm ?? null, page_h_mm: l.page_h_mm ?? null,
    margin_mm: null, font_family: "Inter", is_default: false, auto_print_on: "", body_html: l.body_html };
  const r = await api.post("/documents/templates", row);
  return { ...row, id: r?.id };
}

function Thumb({ api, l }: { api: Api; l: Layout }) {
  const [html, setHtml] = useState<string | null>(null);
  useEffect(() => {
    let on = true;
    api.post("/documents/preview", { body_html: l.body_html, doc_type: l.doc_type }).then((r) => on && setHtml(r?.html || "")).catch(() => on && setHtml(""));
    return () => { on = false; };
  }, [api, l]);
  const narrow = l.page_size.startsWith("receipt") || l.doc_type === "label";
  const w = l.doc_type === "label" ? 300 : narrow ? 302 : 794;
  const scale = narrow ? 0.62 : 0.27;
  const doc = `<!doctype html><html><head><meta charset="utf-8"><style>body{margin:0;padding:${narrow ? 10 : 36}px;font-family:Inter,Arial,sans-serif;font-size:13px;color:#111;background:#fff;line-height:1.5}table{border-collapse:collapse}img{max-width:100%}</style></head><body>${html || ""}</body></html>`;
  return (
    <div className="relative h-52 overflow-hidden bg-white">
      {html === null ? <div className="absolute inset-0 grid place-items-center text-2xs text-ink-tertiary">Loading…</div> : (
        // sandbox="" — no scripts, no navigation; it's only a picture of the page.
        <iframe title={`${l.label} layout`} sandbox="" srcDoc={doc} tabIndex={-1} className="absolute top-0 border-0 pointer-events-none origin-top"
          style={{ width: w, height: 1123, left: "50%", transform: `translateX(-50%) scale(${scale})` }} />
      )}
    </div>
  );
}

export default function LayoutGallery({ api, templates, onCreated, onBlank, onClose }: {
  api: Api; templates: any[]; onCreated: (tpl: any) => void; onBlank: () => void; onClose: () => void;
}) {
  const [layouts, setLayouts] = useState<Layout[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [describe, setDescribe] = useState("");
  useEffect(() => { let on = true; loadLayouts(api, templates).then((l) => on && setLayouts(l)).catch(() => on && setLayouts([])); return () => { on = false; }; }, []); // eslint-disable-line react-hooks/exhaustive-deps

  async function pick(l: Layout) {
    setBusy(l.key); setErr(null);
    try { onCreated(await createFromLayout(api, l, templates)); }
    catch (e: any) { setErr(e?.message || "Couldn't create it"); setBusy(null); }
  }
  async function build() {
    if (!describe.trim()) return;
    setBusy("describe"); setErr(null);
    try {
      const r = await api.post("/documents/ai-generate", { prompt: describe, doc_type: "invoice", preset: "" });
      const isInvoice = /invoice/i.test(describe);
      const l: Layout = { key: "describe", label: isInvoice ? "Invoice" : "New document", hint: "", doc_type: isInvoice ? "invoice" : "other", page_size: "a4", body_html: r?.body_html || "" };
      onCreated(await createFromLayout(api, l, templates));
    } catch (e: any) { setErr(e?.message || "Couldn't build it"); setBusy(null); }
  }

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center p-6" role="dialog" aria-modal="true" aria-label="Start from a layout">
      <div className="absolute inset-0 bg-ink-primary/40" onClick={onClose} aria-hidden="true" />
      <div className="relative w-full max-w-4xl max-h-full overflow-y-auto bg-white rounded-2xl border border-slate-200 shadow-clay-drawer p-6">
        <div className="flex items-start gap-3 mb-4">
          <div className="flex-1">
            <h2 className="text-title font-semibold text-ink-primary">Start from a layout</h2>
            <p className="text-xs text-ink-tertiary">Shown with your shop's logo and a recent job. Pick one, then change anything you like.</p>
          </div>
          <button type="button" onClick={onClose} aria-label="Close" className="w-8 h-8 rounded-lg grid place-items-center text-ink-tertiary hover:bg-slate-100"><X size={16} /></button>
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
          {layouts === null && <div className="col-span-full py-10 text-center text-xs text-ink-tertiary">Loading layouts…</div>}
          {(layouts || []).map((l) => (
            <button key={l.key} type="button" onClick={() => pick(l)} disabled={!!busy}
              className="group text-left rounded-2xl border border-slate-200 overflow-hidden bg-white hover:border-primary hover:shadow-clay-card transition-shadow disabled:opacity-60">
              <Thumb api={api} l={l} />
              <div className="px-3 py-2.5 border-t border-slate-100">
                <div className="text-sm font-semibold text-ink-primary group-hover:text-primary">{busy === l.key ? "Creating…" : l.label}</div>
                <div className="text-2xs text-ink-tertiary">{l.hint}</div>
              </div>
            </button>
          ))}
          {layouts && (
            <button type="button" onClick={onBlank} disabled={!!busy}
              className="rounded-2xl border border-dashed border-slate-300 grid place-items-center min-h-[262px] text-ink-secondary hover:border-slate-400 hover:bg-slate-50">
              <span className="flex flex-col items-center gap-1.5 text-sm font-semibold"><FilePlus2 size={20} />Blank page<span className="text-2xs font-normal text-ink-tertiary">Start from nothing</span></span>
            </button>
          )}
        </div>
        <div className="mt-4 flex items-center gap-2">
          <Sparkles size={15} className="text-primary shrink-0" aria-hidden="true" />
          <input value={describe} onChange={(e) => setDescribe(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") build(); }}
            placeholder="Or describe one, e.g. “invoice with logo, parts table, deposit line and signature”" aria-label="Describe a layout"
            className="glass-input flex-1 rounded-xl px-3 py-2 text-sm" />
          <button type="button" onClick={build} disabled={!!busy || !describe.trim()} className={buttonClass("primary", "md")}>{busy === "describe" ? "Building…" : "Build it"}</button>
        </div>
        {err && <p className="mt-2 text-xs text-rose-700" role="alert">{err}</p>}
      </div>
    </div>
  );
}
