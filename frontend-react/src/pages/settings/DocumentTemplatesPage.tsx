import { useEffect, useState } from "react";
import { M } from "../../components/Shell";
import { api } from "../../api/client";
import { confirmDialog } from "../../lib/dialog";
import PrintSetupCard from "../../components/PrintSetupCard";

/* Settings › Document templates — a gallery of what you print, one section per kind of document,
   each template shown as a real page filled with a recent job (/documents/preview). Kinds with no
   template say so and offer to create one. The editor itself (TemplateEditorDrawer) is unchanged. */

const KINDS: { key: string; label: string; hint: string; icon: string }[] = [
  { key: "work_order", label: "Work order receipt", hint: "Given at check-in", icon: "build" },
  { key: "estimate", label: "Estimate", hint: "Sent for approval", icon: "request_quote" },
  { key: "invoice", label: "Invoice", hint: "What the customer pays", icon: "receipt_long" },
  { key: "credit_note", label: "Credit note", hint: "Money credited back to a client", icon: "undo" },
  { key: "receipt", label: "Receipts", hint: "POS sales and payments", icon: "payments" },
  { key: "label", label: "Labels and price tags", hint: "Stickers for items and shelves", icon: "label" },
  { key: "other", label: "Other documents", hint: "Anything else you print", icon: "description" },
];

const WHEN: Record<string, string> = { create: "job created", close: "job closed", sale: "sale" };
// " · prints by itself: job created, sale" — or nothing when it only prints on request.
function autoText(events?: string[]) {
  const on = (events || []).filter((e) => WHEN[e]);
  return on.length ? ` · prints by itself: ${on.map((e) => WHEN[e]).join(", ")}` : "";
}

function Thumb({ t }: { t: any }) {
  const [html, setHtml] = useState<string | null>(null);
  useEffect(() => {
    let on = true;
    api.post<any>("/documents/preview", { body_html: t.body_html || "", doc_type: t.doc_type })
      .then((r) => on && setHtml(r?.html || "")).catch(() => on && setHtml(""));
    return () => { on = false; };
  }, [t.id, t.body_html, t.doc_type]);
  const label = t.doc_type === "label";
  const doc = `<!doctype html><html><head><meta charset="utf-8"><style>
    body{margin:0;padding:${label ? 18 : 36}px;font-family:${t.font_family || "Inter"},system-ui,sans-serif;font-size:13px;color:#111;background:#fff}
    table{border-collapse:collapse;width:100%}td,th{padding:4px 6px;border-bottom:1px solid #eee;text-align:left}img{max-width:100%}
  </style></head><body>${html || ""}</body></html>`;
  return (
    <div className={`relative overflow-hidden bg-white ${label ? "h-[120px]" : "h-[210px]"}`}>
      {html === null ? <div className="absolute inset-0 grid place-items-center text-[11px] text-ink-tertiary">Loading preview…</div> : (
        // sandbox="" — no scripts, no navigation; it's only a picture of the page.
        <iframe title={`Preview of ${t.name}`} sandbox="" srcDoc={doc} tabIndex={-1}
          className="absolute top-0 left-0 origin-top-left pointer-events-none border-0"
          style={{ width: label ? 520 : 794, height: label ? 300 : 1123, transform: `scale(${label ? 0.4 : 0.3})` }} />
      )}
      <div className="absolute inset-x-0 bottom-0 h-10 bg-gradient-to-t from-white to-transparent" />
    </div>
  );
}

export default function DocumentTemplatesPage({ templates, onOpen, onChanged, onFlash }: {
  templates: any[] | null | undefined; onOpen: (row: any) => void; onChanged: () => void; onFlash: (m: string) => void;
}) {
  const [menu, setMenu] = useState<string | null>(null);
  useEffect(() => {
    if (!menu) return;
    const close = () => setMenu(null);
    window.addEventListener("click", close); return () => window.removeEventListener("click", close);
  }, [menu]);
  const all = templates || [];
  const blocks = all.filter((t) => t.doc_type === "component");

  async function makeDefault(t: any) {
    setMenu(null);
    try { await api.put(`/documents/templates/${t.id}`, { ...t, is_default: true }); onFlash(`${t.name} is now the default`); onChanged(); }
    catch (e: any) { onFlash(e?.message || "Couldn't change the default"); }
  }
  async function duplicate(t: any) {
    setMenu(null);
    try { const { id: _id, ...rest } = t; await api.post("/documents/templates", { ...rest, name: `${t.name} (copy)`, is_default: false }); onFlash("Copy made"); onChanged(); }
    catch (e: any) { onFlash(e?.message || "Couldn't copy"); }
  }
  async function remove(t: any) {
    setMenu(null);
    if (!(await confirmDialog(`Delete "${t.name}"?`, { title: "Delete template", confirmLabel: "Delete", danger: true }))) return;
    try { await api.del(`/documents/templates/${t.id}`); onFlash("Template deleted"); onChanged(); }
    catch (e: any) { onFlash(e?.message || "Couldn't delete"); }
  }

  // A plain function (not a component), so the previews aren't remounted (and re-fetched) on every render.
  const card = (t: any) => (
    <div key={t.id} className="group relative rounded-2xl border border-slate-200/90 bg-white shadow-clay-card overflow-hidden hover:border-slate-300 hover:shadow-clay-drawer transition-shadow">
      <button onClick={() => onOpen(t)} className="block w-full text-left" aria-label={`Edit ${t.name}`}>
        <Thumb t={t} />
        <div className="px-3.5 py-3 border-t border-slate-100">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium text-ink-primary truncate">{t.name}</span>
            {t.is_default && <span className="text-[11px] font-semibold px-1.5 py-0.5 rounded-md bg-emerald-50 text-emerald-700 flex-shrink-0">Default</span>}
          </div>
          <div className="text-[11px] text-ink-tertiary">{t.doc_type === "label" ? "Label" : (t.page_size || "a4").toUpperCase()}{(t.print_statuses || []).length ? ` · only in ${(t.print_statuses || []).length} status${(t.print_statuses || []).length === 1 ? "" : "es"}` : ""}{autoText(t.auto_print_events)}</div>
        </div>
      </button>
      <button aria-label={`More actions for ${t.name}`} onClick={(e) => { e.stopPropagation(); setMenu(menu === t.id ? null : t.id); }}
        className="absolute top-2 right-2 w-8 h-8 rounded-lg grid place-items-center bg-white/90 border border-slate-200 text-ink-secondary opacity-0 group-hover:opacity-100 focus:opacity-100 shadow-sm"><M n="more_horiz" c="text-base" /></button>
      {menu === t.id && (
        <div className="absolute top-11 right-2 z-20 w-44 bg-white rounded-xl border border-slate-200 shadow-clay-drawer py-1 text-xs" onClick={(e) => e.stopPropagation()}>
          <button onClick={() => { setMenu(null); onOpen(t); }} className="w-full text-left px-3 py-2 hover:bg-slate-50 flex items-center gap-2"><M n="edit" c="text-sm" />Edit</button>
          {!t.is_default && <button onClick={() => makeDefault(t)} className="w-full text-left px-3 py-2 hover:bg-slate-50 flex items-center gap-2"><M n="check_circle" c="text-sm" />Use by default</button>}
          <button onClick={() => duplicate(t)} className="w-full text-left px-3 py-2 hover:bg-slate-50 flex items-center gap-2"><M n="content_copy" c="text-sm" />Duplicate</button>
          <button onClick={() => remove(t)} className="w-full text-left px-3 py-2 hover:bg-rose-50 text-rose-600 flex items-center gap-2"><M n="delete" c="text-sm" />Delete</button>
        </div>
      )}
    </div>
  );

  return (
    <div className="max-w-5xl space-y-6 pb-10">
      <div>
        <h2 className="text-base font-semibold text-ink-primary">Documents you print</h2>
        <p className="text-xs text-ink-tertiary">Receipts, estimates, invoices and labels. Each preview is filled with your latest job so you see exactly what customers get.</p>
        <button onClick={() => onOpen(null)} className="mt-3 inline-flex items-center gap-1.5 h-9 px-3.5 rounded-xl bg-primary text-white text-xs font-semibold hover:opacity-90"><M n="add" c="text-sm" />New template</button>
      </div>

      <PrintSetupCard />

      {templates == null && <div className="text-xs text-ink-tertiary">Loading…</div>}
      {templates && KINDS.map((k) => {
        const mine = all.filter((t) => t.doc_type === k.key).sort((a, b) => Number(!!b.is_default) - Number(!!a.is_default));
        if (!mine.length && (k.key === "other" || k.key === "receipt")) return null;
        return (
          <section key={k.key}>
            <div className="flex items-center gap-2 mb-2.5">
              <span className="w-7 h-7 rounded-lg bg-primary/10 text-primary grid place-items-center"><M n={k.icon} c="text-[15px]" /></span>
              <span className="text-sm font-semibold text-ink-primary">{k.label}</span>
              <span className="text-[11px] text-ink-tertiary">{k.hint}</span>
            </div>
            <div className={`grid gap-3 ${k.key === "label" ? "grid-cols-2 sm:grid-cols-3 lg:grid-cols-4" : "grid-cols-2 sm:grid-cols-3 lg:grid-cols-4"}`}>
              {mine.map((t) => card(t))}
              <button onClick={() => onOpen({ doc_type: k.key, name: mine.length ? "" : k.label })}
                className={`rounded-2xl border border-dashed border-slate-300 grid place-items-center text-xs font-semibold text-ink-secondary hover:bg-white hover:border-slate-400 transition-colors ${k.key === "label" ? "min-h-[170px]" : "min-h-[262px]"}`}>
                <span className="flex flex-col items-center gap-1.5 px-4 text-center">
                  <M n="add_circle" c="text-xl" />
                  {mine.length ? "Another design" : <>Create {/^[aeiou]/i.test(k.label) ? "an" : "a"} {k.label.toLowerCase()}<span className="text-[11px] font-normal text-ink-tertiary">None yet. The standard layout is used until you make one.</span></>}
                </span>
              </button>
            </div>
          </section>
        );
      })}

      {blocks.length > 0 && (
        <section>
          <div className="flex items-center gap-2 mb-2.5">
            <span className="w-7 h-7 rounded-lg bg-slate-100 text-ink-secondary grid place-items-center"><M n="dashboard" c="text-[15px]" /></span>
            <span className="text-sm font-semibold text-ink-primary">Reusable blocks</span>
            <span className="text-[11px] text-ink-tertiary">Pieces you drop into several documents, like a footer</span>
          </div>
          <div className="flex flex-wrap gap-2">
            {blocks.map((b) => (
              <button key={b.id} onClick={() => onOpen(b)} className="inline-flex items-center gap-2 px-3 py-2 rounded-xl border border-slate-200 bg-white text-xs text-ink-primary hover:border-slate-300"><M n="dashboard" c="text-sm text-ink-tertiary" />{b.name}</button>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
