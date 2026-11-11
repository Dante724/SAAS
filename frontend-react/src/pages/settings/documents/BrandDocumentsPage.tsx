// Settings › Documents › Brand & details: the simple way to set up everything the shop prints.
// Left: logo, brand colour, style, what shows on documents, terms. Right: the shop's real
// documents, filled in with its latest real records, at the real paper size — click a section to
// hide, move or resize it, click text to type over it, "+ Add detail" for more fields.
// Changes save by themselves (like the editor), into the same templates and fields as before.
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Check, ChevronRight, ImagePlus, LayoutTemplate, Plus, Search, Trash2 } from "lucide-react";
import { api } from "../../../api/client";
import { sanitizeHtml } from "../../../lib/safeHtml";
import Switch from "../../../components/Switch";
import { buttonClass } from "../../../components/Button";
import { cleanTemplate, sameTemplate } from "../templateEditor/templateHtml";
import { applyEdit, editBase, moveSectionAt, setEditCleaner } from "../templateEditor/simpleDoc";
import { FEATURES, SUGGESTED, detailFor, detailState, featureLocked, featureOn, setFeature, toggleDetail, type DocKind } from "../templateEditor/docFeatures";
import DocPreview, { type EditOp } from "./DocPreview";
import { KINDS, PAPERS, STANDARD, STYLES, metaOf, pageBox, payloadFor, pickTemplate, styleOf, withStyle, type Meta } from "./shopDocuments";

// Edits and the preview both work on the template as it will be saved (cleaned), so the numbers
// a click carries always point at the same section.
setEditCleaner((h) => cleanTemplate(h, sanitizeHtml));

export const AUTOSAVE_MS = 2000;
export const BRAND_SWATCHES = ["#0059ba", "#0f766e", "#b91c1c", "#7c3aed", "#1f2937"];
const LOGO_MAX = 1_000_000;

/** `loaded` is the meta as the server last had it from us: a save sends only fields that differ
 *  from it, so settings changed elsewhere in the meantime aren't sent back over. */
type Doc = { kind: DocKind; id: string | null; meta: Meta; loaded: Meta; html: string; saved: { html: string; meta: string } };
/** An edit that ends up where the page started (on then off again) goes back to the saved text
 *  exactly, so an untouched template is never re-saved in a different spelling. */
function settle(d: Doc): Doc {
  return d.html !== d.saved.html && sameTemplate(d.html, d.saved.html) ? { ...d, html: d.saved.html } : d;
}

export default function BrandDocumentsPage({ templates, reload, onOpenEditor, onAllTemplates }: {
  templates: any[] | null | undefined;
  reload: () => void;
  /** open the full Design editor on this template */
  onOpenEditor: (row: any) => void;
  onAllTemplates: () => void;
}) {
  const [docs, setDocs] = useState<Record<string, Doc>>({});
  const [kind, setKind] = useState<DocKind>("invoice");
  // `color` shows on documents only when `docOn` (opt-in; a portal colour alone changes nothing)
  const [brand, setBrand] = useState<{ color: string | null; docOn: boolean; logo: string; note: string } | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [status, setStatus] = useState<"" | "saving" | "saved" | "error">("");
  const [err, setErr] = useState<string | null>(null);
  const [sample, setSample] = useState<string | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const fileRef = useRef<HTMLInputElement | null>(null);
  const docsRef = useRef(docs); docsRef.current = docs;
  const isDirty = (d: Doc) => d.html !== d.saved.html || JSON.stringify(d.meta) !== d.saved.meta;

  // ── load: the shop's brand, and its template (or the standard layout) per document ──
  useEffect(() => {
    Promise.all([api.get<any>("/branding").catch(() => ({})), api.get<any>("/staff/company").catch(() => ({}))])
      .then(([b, c]) => setBrand({ color: b?.primary_color || null, docOn: !!b?.doc_color_on, logo: c?.logo_data || "", note: c?.company_note || "" }));
  }, []);
  useEffect(() => {
    if (!templates) return;
    let on = true;
    (async () => {
      const fresh: Record<string, Doc> = {};
      for (const { kind: k } of KINDS) {
        const t = pickTemplate(templates, k);
        const cur = docsRef.current[k];
        if (cur && isDirty(cur) && cur.id === (t?.id ?? null)) continue;    // decided again below, on the latest state
        let html = t?.body_html ?? "";
        if (!t && STANDARD[k].preset) html = cur && !cur.id ? cur.saved.html : (await api.post<any>("/documents/ai-generate", { prompt: "", doc_type: k, preset: STANDARD[k].preset }).catch(() => null))?.body_html || "";
        const meta = metaOf(t, k);
        fresh[k] = { kind: k, id: t?.id ?? null, meta, loaded: meta, html, saved: { html, meta: JSON.stringify(meta) } };
      }
      // merge into the latest state: work in progress on the same template is never replaced
      if (on) setDocs((all) => {
        const next = { ...all };
        for (const [k, f] of Object.entries(fresh)) { const cur = all[k]; if (!(cur && isDirty(cur) && cur.id === f.id)) next[k] = f; }
        return next;
      });
    })();
    return () => { on = false; };
  }, [templates]); // eslint-disable-line react-hooks/exhaustive-deps

  const doc = docs[kind];
  const change = (k: DocKind, fn: (d: Doc) => Doc) => setDocs((all) => (all[k] ? { ...all, [k]: settle(fn(all[k])) } : all));

  // ── saving: one save at a time, in order, so a document is created once and later saves use
  // its id; about two seconds after the last change; anything left is saved when leaving ──
  const queue = useRef<Promise<unknown>>(Promise.resolve());
  function enqueue<T>(job: () => Promise<T>): Promise<T> {
    const run = queue.current.then(job, job);
    queue.current = run.catch(() => undefined);
    return run;
  }
  async function saveDirty(onlyKind?: DocKind, create = false): Promise<void> {
    const todo = Object.values(docsRef.current).filter((d) => (onlyKind ? d.kind === onlyKind : true) && (isDirty(d) || (create && !d.id)));
    if (!todo.length) return;
    if (mounted.current) { setStatus("saving"); setErr(null); }
    try {
      const rows: any[] = (await api.get<any>("/documents/templates").catch(() => null))?.items || [];
      let created = false;
      for (const snap of todo) {
        const d = docsRef.current[snap.kind] || snap;                 // the latest copy (it may have an id by now)
        const row = d.id ? rows.find((r) => r.id === d.id) || null : null;
        const changedBody = d.html !== d.saved.html || !row;
        const payload = payloadFor(row, d.meta, d.loaded, changedBody ? cleanTemplate(d.html, sanitizeHtml) : row.body_html);
        let id = d.id;
        if (id) await api.put(`/documents/templates/${id}`, payload);
        else { id = (await api.post<any>("/documents/templates", payload))?.id || null; created = true; }  // first change makes the shop's own copy
        const savedNow = { html: d.html, meta: JSON.stringify(d.meta) };
        docsRef.current = { ...docsRef.current, [d.kind]: { ...d, id, loaded: d.meta, saved: savedNow } };
        if (mounted.current) setDocs((all) => (all[d.kind] ? { ...all, [d.kind]: { ...all[d.kind], id, loaded: d.meta, saved: savedNow } } : all));
      }
      if (mounted.current) setStatus("saved");
      if (created) reload();
    } catch (e: any) { if (mounted.current) { setStatus("error"); setErr(e?.message || "Couldn't save"); } }
  }
  const mounted = useRef(true);
  useEffect(() => {
    if (!Object.values(docs).some(isDirty)) return;
    const t = setTimeout(() => { enqueue(() => saveDirty()); }, AUTOSAVE_MS);
    return () => clearTimeout(t);
  }, [docs]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    mounted.current = true;
    const warn = (e: BeforeUnloadEvent) => {
      if (!Object.values(docsRef.current).some(isDirty)) return;
      enqueue(() => saveDirty());
      e.preventDefault(); e.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => {
      window.removeEventListener("beforeunload", warn);
      mounted.current = false;
      enqueue(() => saveDirty());                                   // leaving the page: save what's left
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // ── brand (shop-wide; every preview refreshes) ──
  /** Picking a colour here turns it on for documents; "No colour" turns documents off and leaves
   *  the colour itself (the white-label portal may use it). */
  async function saveColor(color: string | null) {
    setBrand((b) => (b ? (color ? { ...b, color, docOn: true } : { ...b, docOn: false }) : b));
    try { await api.put("/branding", color ? { primary_color: color, doc_color_on: true } : { doc_color_on: false }); setRefresh((n) => n + 1); }
    catch (e: any) { setErr(e?.message || "Couldn't save the colour"); }
  }
  async function saveCompany(patch: { logo_data?: string; company_note?: string }) {
    try { await api.put("/staff/company", patch); setRefresh((n) => n + 1); }
    catch (e: any) { setErr(e?.message || "Couldn't save"); }
  }
  const noteTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  function setNote(v: string) {
    setBrand((b) => (b ? { ...b, note: v } : b));
    if (noteTimer.current) clearTimeout(noteTimer.current);
    noteTimer.current = setTimeout(() => saveCompany({ company_note: v }), 1200);
  }
  function pickLogo(f: File | undefined) {
    if (!f) return;
    if (!/^image\/(png|jpe?g|gif|webp)$/.test(f.type)) { setErr("Use a PNG, JPG, GIF or WebP image for the logo."); return; }
    if (f.size > LOGO_MAX) { setErr("That image is over 1 MB. Use a smaller logo."); return; }
    const r = new FileReader();
    r.onload = () => { const logo = String(r.result || ""); setBrand((b) => (b ? { ...b, logo } : b)); saveCompany({ logo_data: logo }); };
    r.readAsDataURL(f);
  }
  /** Style applies to every document. */
  function chooseStyle(id: string) {
    // A document the shop hasn't made its own yet just previews in the style (nothing is created
    // for it); the shop's own templates save it.
    setDocs((all) => Object.fromEntries(Object.entries(all).map(([k, d]) => {
      const meta = withStyle(d.meta, id);
      return [k, d.id ? { ...d, meta } : { ...d, meta, loaded: meta, saved: { ...d.saved, meta: JSON.stringify(meta) } }];
    })));
  }
  /** A "Show on documents" switch applies to every document that can show it. */
  function toggleFeature(key: string, on: boolean) {
    const f = FEATURES.find((x) => x.key === key)!;
    // Every document the shop has, plus the one on screen (a standard layout becomes the shop's own
    // copy only when the owner changes it while looking at it).
    setDocs((all) => Object.fromEntries(Object.entries(all).map(([k, d]) => [k,
      (d.id || d.kind === kind) && f.snippet(d.kind) && !featureLocked(d.html, f) ? settle({ ...d, html: setFeature(d.html, f, d.kind, on) }) : d])));
  }

  /** A click on the preview. `source` is the text the preview numbered; a click made against an
   *  older page is ignored. A move answers where the section went. */
  function onEdit(op: EditOp, source: string): number | void {
    const d = docsRef.current[kind]; if (!d) return;
    let out = applyEdit(d.html, op, source); if (out == null) return;
    let moved: number | undefined;
    if (op.type === "move") { const r = moveSectionAt(editBase(d.html), op.i, op.dir); out = r.html; moved = r.i; }
    const next = out;
    docsRef.current = { ...docsRef.current, [kind]: settle({ ...d, html: next }) };
    change(kind, (x) => (x.html === d.html ? { ...x, html: next } : x));
    return moved;
  }
  async function openEditor() {
    if (!docsRef.current[kind]) return;
    // the pending save goes first (and makes the shop's copy if there isn't one yet), never twice
    await enqueue(() => saveDirty(kind, true));
    const d = docsRef.current[kind];
    if (!d?.id) { setErr("Couldn't open the editor"); return; }
    onOpenEditor({ ...d.meta, id: d.id, body_html: d.html });
  }
  const source = useMemo(() => (doc ? editBase(doc.html) : ""), [doc?.html]); // eslint-disable-line react-hooks/exhaustive-deps

  // what each switch shows for the document on screen (worked out once per change of the page)
  const featureStates = useMemo(() => FEATURES.map((f) => {
    const can = !!doc && !!f.snippet(doc.kind);
    const on = !!doc && featureOn(doc.html, f);
    const locked = doc && can ? featureLocked(doc.html, f) : null;
    return { f, can, on, locked, reason: !doc ? null : !can ? f.reason(doc.kind) : locked };
  }), [doc?.html, doc?.kind]); // eslint-disable-line react-hooks/exhaustive-deps
  const style = doc ? styleOf(doc.meta) : null;
  const box = doc ? pageBox(doc.meta) : { w: 794, h: 1123, pad: 38 };
  const anyDirty = Object.values(docs).some(isDirty);
  const statusText = status === "saving" ? "Saving…" : status === "error" ? "Not saved" : anyDirty ? "Unsaved changes" : status === "saved" ? "All changes saved" : "";

  return (
    <div className="grid grid-cols-1 xl:grid-cols-[340px_minmax(0,1fr)] gap-5 pb-10">
      {/* ── left: brand & details ── */}
      <div className="space-y-4">
        <div>
          <h2 className="text-base font-semibold text-ink-primary">Brand &amp; details</h2>
          <p className="text-xs text-ink-tertiary">Set it once; every document you print follows.</p>
        </div>

        <Card title="Logo">
          <div className="flex items-center gap-3">
            <button type="button" onClick={() => fileRef.current?.click()} aria-label={brand?.logo ? "Change logo" : "Add logo"}
              className="w-20 h-20 rounded-2xl border-2 border-dashed border-slate-300 grid place-items-center overflow-hidden bg-slate-50 hover:border-primary/60 shrink-0">
              {brand?.logo ? <img src={brand.logo} alt="Shop logo" className="w-full h-full object-contain" /> : <ImagePlus size={20} className="text-ink-tertiary" aria-hidden />}
            </button>
            <div className="text-xs text-ink-secondary space-y-1">
              <button type="button" onClick={() => fileRef.current?.click()} className={buttonClass("secondary", "sm")}>{brand?.logo ? "Change logo" : "Upload logo"}</button>
              {brand?.logo && <button type="button" onClick={() => { setBrand((b) => (b ? { ...b, logo: "" } : b)); saveCompany({ logo_data: "" }); }} className="flex items-center gap-1 text-2xs font-semibold text-rose-600 hover:underline"><Trash2 size={12} aria-hidden /> Remove</button>}
              <p className="text-2xs text-ink-tertiary">PNG or JPG, up to 1 MB. Also used on your public pages.</p>
            </div>
            <input ref={fileRef} type="file" accept="image/png,image/jpeg,image/gif,image/webp" className="hidden" aria-label="Logo file" onChange={(e) => pickLogo(e.target.files?.[0])} />
          </div>
        </Card>

        <Card title="Brand colour">
          <div className="flex items-center gap-2" role="group" aria-label="Brand colour">
            {BRAND_SWATCHES.map((c) => (
              <button key={c} type="button" aria-label={`Brand colour ${c}`} aria-pressed={(brand?.docOn && brand?.color?.toLowerCase() === c)}
                onClick={() => saveColor(c)} className={`w-8 h-8 rounded-full grid place-items-center ${(brand?.docOn && brand?.color?.toLowerCase() === c) ? "ring-2 ring-offset-2 ring-primary" : "ring-1 ring-slate-300"}`} style={{ background: c }}>
                {(brand?.docOn && brand?.color?.toLowerCase() === c) && <Check size={14} className="text-white" aria-hidden />}
              </button>
            ))}
            <label className="w-8 h-8 rounded-full border border-dashed border-slate-300 relative cursor-pointer grid place-items-center text-ink-tertiary" title="Your own colour">
              <Plus size={14} aria-hidden />
              <input type="color" aria-label="Your own colour" value={/^#[0-9a-f]{6}$/i.test(brand?.color || "") ? brand!.color! : "#0059ba"} onChange={(e) => saveColor(e.target.value)} className="absolute inset-0 opacity-0 cursor-pointer" />
            </label>
            {brand?.docOn && <button type="button" onClick={() => saveColor(null)} className="text-2xs font-semibold text-ink-tertiary hover:text-ink-primary ml-1">No colour</button>}
          </div>
          <p className="text-2xs text-ink-tertiary mt-2">Colours headings, table headers and dividers on every document.</p>
        </Card>

        <Card title="Style">
          <div className="grid grid-cols-3 gap-2" role="group" aria-label="Style">
            {STYLES.map((s) => (
              <button key={s.id} type="button" aria-pressed={style === s.id} onClick={() => chooseStyle(s.id)}
                className={`rounded-xl border px-2 py-2.5 text-left ${style === s.id ? "border-primary bg-primary/5" : "border-slate-200 hover:border-slate-300"}`}>
                <div className="text-sm font-semibold text-ink-primary" style={{ fontFamily: `${s.font}, sans-serif` }}>Aa</div>
                <div className="text-xs font-semibold text-ink-primary">{s.label}</div>
                <div className="text-2xs text-ink-tertiary">{s.hint}</div>
              </button>
            ))}
          </div>
        </Card>

        <Card title="Show on documents">
          <ul className="divide-y divide-slate-100">
            {featureStates.map(({ f, can, on, locked, reason }) => {
              return (
                <li key={f.key} className="py-2" data-testid={`feature-${f.key}`}>
                  <div className={`flex items-center gap-3 ${can && !locked ? "" : "opacity-60"}`}>
                    <span className="flex-1 text-sm text-ink-primary">{f.label}</span>
                    <Switch size="sm" label={f.label} on={on} disabled={!can || !!locked} onChange={(v) => toggleFeature(f.key, v)} />
                  </div>
                  {reason && <p className="text-2xs text-ink-tertiary mt-0.5">{reason}</p>}
                </li>
              );
            })}
          </ul>
        </Card>

        <Card title="Terms and footer">
          <textarea value={brand?.note ?? ""} onChange={(e) => setNote(e.target.value)} rows={4} aria-label="Terms and footer text"
            placeholder="e.g. All repairs carry a 90-day warranty on parts and labour."
            className="glass-input w-full rounded-xl px-3 py-2 text-sm" />
          <p className="text-2xs text-ink-tertiary mt-1">Prints where “Warranty and terms” is on.</p>
        </Card>
      </div>

      {/* ── right: the shop's real documents ── */}
      <div className="min-w-0 rounded-2xl border border-slate-200 bg-slate-100 flex flex-col">
        <div className="px-4 pt-3 pb-2 flex flex-wrap items-center gap-2 border-b border-slate-200 bg-white rounded-t-2xl">
          <div className="flex flex-wrap gap-1.5" role="group" aria-label="Document">
            {KINDS.map((k) => (
              <button key={k.kind} type="button" aria-pressed={kind === k.kind} onClick={() => { setKind(k.kind); setAddOpen(false); }}
                className={`h-8 px-3 rounded-full ring-1 ring-inset text-xs font-semibold ${kind === k.kind ? "bg-primary/10 text-primary ring-primary/20" : "bg-white text-ink-secondary ring-slate-200 hover:text-ink-primary"}`}>{k.label}</button>
            ))}
          </div>
          <span className="ml-auto text-2xs text-ink-tertiary" role="status" data-testid="docs-status">{statusText}</span>
        </div>
        {doc && (
          <div className="px-4 py-2 flex flex-wrap items-center gap-3 border-b border-slate-200 bg-white">
            {doc.kind === "label" ? (
              <span className="text-xs text-ink-secondary" data-testid="paper">Label {doc.meta.page_w_mm || 50} × {doc.meta.page_h_mm || 30} mm <span className="text-ink-tertiary">· change the size in Customise layout</span></span>
            ) : (
              <div className="flex items-center gap-2"><span className="text-xs font-semibold text-ink-secondary">Paper</span>
                <div className="inline-flex rounded-lg bg-slate-100 p-0.5 gap-0.5" role="group" aria-label="Paper" data-testid="paper">
                  {PAPERS.map((p) => (
                    <button key={p.id} type="button" aria-pressed={doc.meta.page_size === p.id} onClick={() => change(kind, (d) => ({ ...d, meta: { ...d.meta, page_size: p.id } }))}
                      className={`h-7 px-2.5 rounded-md text-xs font-semibold ${doc.meta.page_size === p.id ? "bg-white text-primary shadow-2xs" : "text-ink-secondary hover:text-ink-primary"}`}>{p.label}</button>
                  ))}
                </div>
              </div>
            )}
            <div className="flex items-center gap-2 opacity-60" title="The number of copies is chosen when you print, for now.">
              <span className="text-xs font-semibold text-ink-secondary">Copies</span>
              <input type="number" value={1} disabled aria-label="Print copies" className="glass-input w-14 h-8 rounded-lg px-2 text-sm" />
              <span className="text-2xs text-ink-tertiary">Chosen when you print, for now.</span>
            </div>
            <div className="relative ml-auto">
              <button type="button" onClick={() => setAddOpen((o) => !o)} aria-expanded={addOpen} className={buttonClass("secondary", "sm")}><Plus size={14} aria-hidden /> Add detail</button>
              {addOpen && <AddDetail html={doc.html} kind={doc.kind} onToggle={(d) => change(kind, (x) => ({ ...x, html: toggleDetail(x.html, d) }))} onClose={() => setAddOpen(false)} />}
            </div>
          </div>
        )}
        <div className="flex-1 py-6">
          {doc ? (
            <DocPreview key={doc.kind} post={(p, b) => api.post<any>(p, b)} source={source} kind={doc.kind} page={box} font={doc.meta.font_family} refresh={refresh} onEdit={onEdit} onSample={setSample} />
          ) : <div className="py-20 text-center text-xs text-ink-tertiary">Loading your documents…</div>}
        </div>
        <div className="px-4 py-3 border-t border-slate-200 bg-white rounded-b-2xl flex flex-wrap items-center gap-3">
          <button type="button" onClick={openEditor} disabled={!doc} className={buttonClass("secondary", "md")}><LayoutTemplate size={15} aria-hidden /> Customise layout</button>
          <button type="button" onClick={onAllTemplates} className="text-xs font-semibold text-primary hover:underline inline-flex items-center gap-0.5">All templates <ChevronRight size={13} aria-hidden /></button>
          <span className="ml-auto text-2xs text-ink-tertiary">{sample ? `Filled in from ${sample}. ` : ""}Click a section to hide, move or resize it; click text to type.</span>
          {err && <span className="w-full text-xs text-rose-700" role="alert">{err}</span>}
        </div>
      </div>
    </div>
  );
}

function Card({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="rounded-2xl border border-slate-200 bg-white p-4" aria-label={title}>
      <h3 className="text-sm font-semibold text-ink-primary mb-2.5">{title}</h3>
      {children}
    </section>
  );
}

function AddDetail({ html, kind, onToggle, onClose }: { html: string; kind: DocKind; onToggle: (d: { label: string; tok: string }) => void; onClose: () => void }) {
  const [q, setQ] = useState("");
  const [all, setAll] = useState<string[]>([]);
  const ref = useRef<HTMLDivElement | null>(null);
  useEffect(() => { api.get<any>("/documents/templates").then((d) => setAll((d?.variable_groups || []).flatMap((g: any) => g.items))).catch(() => setAll([])); }, []);
  useEffect(() => {
    const close = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) onClose(); };
    window.addEventListener("mousedown", close); return () => window.removeEventListener("mousedown", close);
  }, [onClose]);
  const ql = q.trim().toLowerCase();
  const found = useMemo(() => (ql ? Array.from(new Set(all)).filter((t) => t.toLowerCase().includes(ql)).slice(0, 30) : []), [all, ql]);
  const row = (d: { label: string; tok: string }) => {
    const st = detailState(html, d.tok);
    return (
      <li key={d.tok}>
        <button type="button" disabled={st === "layout"} onClick={() => onToggle(d)} aria-pressed={st === "ours"}
          className="w-full flex items-center gap-2 px-2 py-1.5 rounded-lg text-left text-sm text-ink-primary hover:bg-slate-50 disabled:opacity-60">
          <span className={`w-4 h-4 rounded border grid place-items-center ${st ? "bg-primary border-primary text-white" : "border-slate-300"}`}>{st && <Check size={11} aria-hidden />}</span>
          <span className="flex-1">{d.label}</span>
          {st === "layout" && <span className="text-2xs text-ink-tertiary">Already on the page</span>}
        </button>
      </li>
    );
  };
  return (
    <div ref={ref} role="dialog" aria-label="Add detail" className="absolute right-0 top-10 z-30 w-80 bg-white border border-slate-200 rounded-xl shadow-drawer p-2">
      <div className="px-1 pb-1 text-2xs font-semibold text-ink-tertiary">Suggested for this document</div>
      <ul>{SUGGESTED[kind].map(row)}</ul>
      <label className="mt-2 flex items-center gap-1.5 px-2 h-8 rounded-lg border border-slate-200 focus-within:border-primary">
        <Search size={14} className="text-ink-tertiary" aria-hidden />
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search all fields" aria-label="Search all fields" className="flex-1 bg-transparent text-xs outline-none text-ink-primary" />
      </label>
      {ql && <ul className="max-h-56 overflow-y-auto mt-1">{found.length ? found.map((t) => row(detailFor(t))) : <li className="px-2 py-2 text-xs text-ink-tertiary">No fields match.</li>}</ul>}
    </div>
  );
}
