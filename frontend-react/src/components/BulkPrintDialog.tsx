import { useEffect, useState } from "react";
import { M } from "./Shell";
import { api } from "../api/client";
import { printHtmlInFrame } from "../lib/safeHtml";

/**
 * 144ie — one dialog for bulk exact-size printing of any record type. It loads the
 * matching templates, renders the selection server-side, and prints each record on
 * its own physical page via a hidden iframe. Reused by Clients and Invoices; the
 * product/asset tags in InventoryHub predate it and keep their own copy.
 */
type Props = {
  open: boolean;
  onClose: () => void;
  /** ids of the selected records */
  ids: string[];
  /** noun shown in the dialog, e.g. "client" or "invoice" */
  noun: string;
  /** POST endpoint that renders the batch */
  endpoint: string;
  /** payload key that carries the ids, e.g. "client_ids" | "invoice_ids" */
  idKey: string;
  /** doc_types offered in the template picker */
  docTypes: string[];
  /** show a per-record copies input (labels yes, full invoices no) */
  copies?: boolean;
};

export default function BulkPrintDialog({ open, onClose, ids, noun, endpoint, idKey, docTypes, copies = true }: Props) {
  const [tpls, setTpls] = useState<any[]>([]);
  const [tplId, setTplId] = useState("");
  const [nCopies, setNCopies] = useState(1);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    (async () => {
      try {
        const d = await api.get<any>("/documents/templates");
        const items = (d?.items || []).filter((t: any) => docTypes.includes(t.doc_type));
        setTpls(items); setTplId(items[0]?.id || "");
      } catch { setTpls([]); }
    })();
  }, [open]);   // eslint-disable-line react-hooks/exhaustive-deps

  async function run() {
    if (!tplId) return;
    setBusy(true);
    const body: any = { template_id: tplId, [idKey]: ids };
    if (copies) body.copies = nCopies || 1;
    try {
      const r = await api.post<any>(endpoint, body);
      const html = r?.html;
      if (html) {
        printHtmlInFrame(html);
      }
      onClose();
    } catch { /* noop */ } finally { setBusy(false); }
  }

  if (!open) return null;
  const total = copies ? ids.length * nCopies : ids.length;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink-primary/40 backdrop-blur-[2px]" onClick={onClose}>
      <div className="bg-white rounded-2xl shadow-drawer w-[380px] max-w-full p-5" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-3">
          <h3 className="font-bold text-ink-primary">Print {noun}s</h3>
          <button aria-label="Close" onClick={onClose} className="w-7 h-7 rounded-lg hover:bg-slate-100 grid place-items-center text-ink-tertiary"><M n="close" c="text-base" /></button>
        </div>
        <p className="text-xs text-ink-tertiary mb-3">{ids.length} {noun}{ids.length === 1 ? "" : "s"} selected. Each prints at the template's exact size.</p>
        <label className="block text-[11px] font-semibold text-ink-tertiary mb-1">Template</label>
        <select value={tplId} onChange={(e) => setTplId(e.target.value)} className="w-full bg-white border border-slate-200 rounded-lg text-sm px-2.5 py-2 mb-3">
          {tpls.length === 0 && <option value="">No matching templates — create one in Settings</option>}
          {tpls.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
        </select>
        {copies && (<>
          <label className="block text-[11px] font-semibold text-ink-tertiary mb-1">Copies each</label>
          <input type="number" min={1} max={500} value={nCopies} onChange={(e) => setNCopies(Math.max(1, +e.target.value || 1))} className="w-full bg-white border border-slate-200 rounded-lg text-sm px-2.5 py-2 mb-4" />
        </>)}
        <div className="flex justify-end gap-2">
          <button onClick={onClose} className="px-4 py-2 rounded-lg border border-slate-200 text-ink-secondary hover:bg-slate-100 font-semibold text-xs">Cancel</button>
          <button onClick={run} disabled={!tplId || busy} className="px-4 py-2 rounded-lg bg-primary text-white font-semibold text-xs hover:bg-primary-container disabled:opacity-60 flex items-center gap-1"><M n="print" c="text-[15px]" /> Print {total} {total === 1 ? "page" : "pages"}</button>
        </div>
      </div>
    </div>
  );
}
