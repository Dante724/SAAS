import { useEffect, useState } from "react";
import { M } from "./Shell";
import { api } from "../api/client";
import { BARCODE_TYPES } from "../lib/barcodes";
import { printHtmlInFrame } from "../lib/safeHtml";

/**
 * ii144c — one "Print labels" dialog for bins and serialized units: pick a label template, how many
 * copies, and (optionally) the barcode type, then print the sheet the server renders.
 * `render` posts to the right /documents/render-*-labels endpoint and returns its HTML.
 */
export default function LabelPrintDialog({ title, count, noun, typeChoice, defaultType = "", prefer, render, onClose }: {
  title: string; count: number; noun: [string, string];
  prefer?: string;                     // the template picked first when the shop has it, e.g. "Bin label"
  typeChoice?: "own" | "required";     // own: "each one's own type" first; required: pick one (default Code 128)
  defaultType?: string;
  render: (templateId: string, copies: number, barcodeType: string) => Promise<{ html?: string }>;
  onClose: () => void;
}) {
  const [tpls, setTpls] = useState<{ id: string; name: string }[] | null>(null);
  const [tplId, setTplId] = useState("");
  const [copies, setCopies] = useState(1);
  const [kind, setKind] = useState(defaultType || (typeChoice === "required" ? "code128" : ""));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    api.get<any>("/documents/templates").then((d) => {
      const items = (d?.items || []).filter((t: any) => ["label", "product_label", "price_tag"].includes(t.doc_type));
      setTpls(items); setTplId((items.find((t: any) => t.name === prefer) || items[0])?.id || "");
    }).catch(() => setTpls([]));
  }, []);
  async function print() {
    if (!tplId) return;
    setBusy(true); setErr(null);
    try {
      const r = await render(tplId, copies, kind);
      if (r?.html) { printHtmlInFrame(r.html); onClose(); }
    } catch (e: any) { setErr(e?.message || "Couldn't print these labels"); }
    finally { setBusy(false); }
  }
  const total = count * copies;
  const fld = "w-full bg-white border border-slate-200 rounded-lg text-sm px-2.5 py-2 mb-3";
  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center p-4" role="dialog" aria-modal="true" aria-label={title}>
      <div className="absolute inset-0 bg-ink-primary/40 backdrop-blur-[2px]" onClick={onClose} />
      <div className="relative w-full max-w-sm bg-white rounded-2xl shadow-drawer border border-slate-200 p-5">
        <div className="flex items-center justify-between mb-3"><h3 className="font-bold text-ink-primary">{title}</h3><button aria-label="Close" onClick={onClose} className="w-7 h-7 rounded-lg hover:bg-slate-100 grid place-items-center text-ink-tertiary"><M n="close" c="text-base" /></button></div>
        <p className="text-xs text-ink-tertiary mb-3">{count} {count === 1 ? noun[0] : noun[1]} selected. Each label prints at the template's exact size.</p>
        <label className="block text-[11px] font-semibold text-ink-tertiary mb-1">Label template</label>
        <select value={tplId} onChange={(e) => setTplId(e.target.value)} className={fld}>
          {tpls?.length === 0 && <option value="">No label templates — create one in Settings › Documents</option>}
          {(tpls || []).map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
        </select>
        {typeChoice && (<>
          <label className="block text-[11px] font-semibold text-ink-tertiary mb-1">Barcode type</label>
          <select value={kind} onChange={(e) => setKind(e.target.value)} className={fld}>
            {typeChoice === "own" && <option value="">Each {noun[0]}'s own type</option>}
            {BARCODE_TYPES.map((t) => <option key={t.key} value={t.key}>{t.label}</option>)}
          </select>
        </>)}
        <label className="block text-[11px] font-semibold text-ink-tertiary mb-1">Copies each</label>
        <input type="number" min={1} max={50} value={copies} onChange={(e) => setCopies(Math.max(1, Math.min(50, +e.target.value || 1)))} className={fld} />
        {err && <div role="alert" className="mb-3 rounded-lg bg-rose-50 border border-rose-200 text-rose-700 text-xs px-3 py-2">{err}</div>}
        <div className="flex gap-2">
          <button onClick={print} disabled={!tplId || busy} className="px-4 py-2 rounded-lg bg-primary text-white font-semibold text-xs hover:bg-primary-container disabled:opacity-60 flex items-center gap-1"><M n="print" c="text-[15px]" />{busy ? "Preparing…" : `Print ${total} label${total === 1 ? "" : "s"}`}</button>
          <button onClick={onClose} className="px-4 py-2 rounded-lg border border-slate-200 text-ink-secondary hover:bg-slate-100 font-semibold text-xs">Cancel</button>
        </div>
      </div>
    </div>
  );
}
