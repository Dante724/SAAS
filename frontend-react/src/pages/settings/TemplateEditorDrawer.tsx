import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import TeklySelect from "../../components/TeklySelect";
import Tabs from "../../components/Tabs";
import { buttonClass } from "../../components/Button";
import LabelBarcodePicker, { LABEL_DOC_TYPES } from "./LabelBarcodePicker";
import { M } from "../../components/Shell";
import { api } from "../../api/client";
import { confirmDialog } from "../../lib/dialog";
import Switch from "../../components/Switch";
import { printHtmlInFrame, sanitizeHtml } from "../../lib/safeHtml";
import { allReasons } from "./templateEditor/modeSupport";
import { cleanTemplate, type ModeId } from "./templateEditor/templateHtml";
import VariableMenu, { type VarGroup } from "./templateEditor/VariableMenu";
import type { PageBox } from "./templateEditor/types";

/* Full-screen document template editor. One stored HTML template, three ways to edit it:
     Design — drag-and-drop page builder (GrapesJS, loaded on demand)
     Text   — word-processor editing (Tiptap, loaded on demand)
     HTML   — the source itself
   plus Preview (filled in by the server with a recent job). Every view reads and writes the same
   HTML; a view that would change a template's markup is greyed out with the reason, and an
   untouched template saves exactly as it was loaded. */

const DesignMode = lazy(() => import("./templateEditor/DesignMode"));
const TextMode = lazy(() => import("./templateEditor/TextMode"));

const MOST_USED = ["{Company Name}", "{Company Logo}", "{Company Phone}", "{Client Name}", "{Client Phone}", "{Order Number}",
  "{Order Created Date}", "{Asset Brand}", "{Asset Model}", "{Asset Serial Number}", "{Order Complaint}", "{Items Table}",
  "{All Items Table}", "{Order Total}", "{Order Balance Due}", "{Barcode}"];
const DOC_TYPES = ["estimate", "invoice", "credit_note", "receipt", "work_order", "label", "other"];
// When a document of this type can print by itself (the server checks the same rule).
const AUTO_EVENTS: [string, string][] = [["create", "A job is created"], ["close", "A job is closed"], ["sale", "A sale is made"]];
export function autoPrintChoices(docType: string): [string, string][] {
  if (docType === "credit_note" || docType === "component") return [];
  if (docType === "receipt" || docType === "other") return AUTO_EVENTS;
  return AUTO_EVENTS.filter(([ev]) => ev !== "sale");
}
function keepAllowed(current: string | null | undefined, docType: string): string {
  const ok = autoPrintChoices(docType).map(([e]) => e);
  return (current || "").split(",").filter((e) => ok.includes(e)).join(",");
}
export function toggleEvent(current: string | null | undefined, ev: string): string {
  const on = new Set((current || "").split(",").filter(Boolean));
  if (on.has(ev)) on.delete(ev); else on.add(ev);
  return AUTO_EVENTS.map(([e]) => e).filter((e) => on.has(e)).join(",");
}
const FONTS = ["Inter", "Arial", "Helvetica", "Georgia", "Times New Roman", "Courier New", "Verdana", "Tahoma", "Roboto", "Trebuchet MS"];

type View = ModeId | "preview";
const MODE_KEY = "tk_template_view";
const MODE_NAME: Record<ModeId, string> = { design: "Design", text: "Text", html: "HTML" };
type Reasons = Record<ModeId, string | null> | null;

/** The view to open a template in: the last one used if it can hold this template, else Design,
 *  then Text, then HTML. */
export function pickView(reasons: Record<ModeId, string | null>, preferred: string | null): ModeId {
  const order: ModeId[] = ["design", "text", "html"];
  if (preferred && order.includes(preferred as ModeId) && !reasons[preferred as ModeId]) return preferred as ModeId;
  return order.find((m) => !reasons[m]) || "html";
}

export default function TemplateEditorDrawer({ initial, onClose, reload }: {
  initial: any; onClose: () => void; reload?: () => void;
}) {
  const [meta, setMeta] = useState({
    name: initial?.name || "", doc_type: initial?.doc_type || "estimate",
    page_size: initial?.page_size || "a4", font_family: initial?.font_family || "Inter",
    is_default: !!initial?.is_default,
    auto_print_on: (initial?.auto_print_on || "") as string,
    page_w_mm: initial?.page_w_mm ?? null as number | null,
    page_h_mm: initial?.page_h_mm ?? null as number | null,
    margin_mm: initial?.margin_mm ?? null as number | null,
    barcode_field: (initial?.barcode_field ?? null) as string | null,
    barcode_types: (initial?.barcode_types || {}) as Record<string, string>,
  });
  // The template body, exactly as stored. Every view reads and writes this one string.
  const [html, setHtml] = useState<string>(initial?.body_html || "");
  const [epoch, setEpoch] = useState(0);            // bump to reload the visual views from `html`
  const [view, setView] = useState<View | null>(null);
  const [reasons, setReasons] = useState<Reasons>(null);
  const [templates, setTemplates] = useState<any[]>([]);
  const [currentId, setCurrentId] = useState<string | null>(initial?.id || null);
  const [actionsOpen, setActionsOpen] = useState(false);
  const [flash, setFlash] = useState<string | null>(null);
  const say = (m: string) => { setFlash(m); setTimeout(() => setFlash(null), 1800); };
  const [vars, setVars] = useState(false);
  const [settings, setSettings] = useState(false);
  const [groups, setGroups] = useState<VarGroup[]>([]);
  const [q, setQ] = useState("");
  const [preview, setPreview] = useState("");
  const [sample, setSample] = useState<string | null>(null);
  const [showSmart, setShowSmart] = useState(false);
  const [aiPrompt, setAiPrompt] = useState("");
  const [aiBusy, setAiBusy] = useState(false);
  const [presets, setPresets] = useState<{ key: string; label: string; doc_type: string }[]>([]);
  const [blankOk, setBlankOk] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const insertRef = useRef<((tok: string) => void) | null>(null);
  const bindInsert = useCallback((fn: ((tok: string) => void) | null) => { insertRef.current = fn; }, []);
  const flushRef = useRef<(() => Promise<string | null>) | null>(null);
  const bindFlush = useCallback((fn: (() => Promise<string | null>) | null) => { flushRef.current = fn; }, []);
  /** The template right now, including text still being typed in the Design canvas. */
  async function latestHtml(): Promise<string> {
    const out = await flushRef.current?.();
    if (out != null && out !== html) { setHtml(out); return out; }
    return html;
  }
  const codeRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => { api.get<any>("/documents/smart-presets").then((d) => setPresets(d?.items || [])).catch(() => setPresets([])); }, []);
  useEffect(() => { api.get<any>("/documents/templates").then((d) => { setGroups(d?.variable_groups || []); setTemplates(d?.items || []); }).catch(() => setGroups([])); }, []);

  // Which views can hold this template. Rechecked shortly after every change.
  useEffect(() => {
    let live = true;
    const t = setTimeout(() => {
      allReasons(cleanTemplate(html, sanitizeHtml)).then((r) => {
        if (!live) return;
        setReasons(r);
        setView((v) => v ?? pickView(r, readPref()));
      }).catch(() => { if (live) { setReasons({ design: null, text: null, html: null }); setView((v) => v ?? "html"); } });
    }, view ? 500 : 0);
    return () => { live = false; clearTimeout(t); };
  }, [html, epoch]); // eslint-disable-line react-hooks/exhaustive-deps

  function readPref() { try { return localStorage.getItem(MODE_KEY); } catch { return null; } }
  async function choose(v: View) {
    await latestHtml();
    if (v !== "preview") { try { localStorage.setItem(MODE_KEY, v); } catch { /* private window */ } }
    if (v === "preview") fetchPreview();
    setView(v);
  }

  /** Replace the whole body from outside the views (open a template, Smart builder). */
  function loadBody(next: string) { setHtml(next); setReasons(null); setView(null); setEpoch((e) => e + 1); }

  async function buildDoc(preset?: string) {
    if (!preset && !aiPrompt.trim()) return;
    if (html.trim() && !(await confirmDialog("Replace the current document?"))) return;
    setAiBusy(true); setErr(null);
    try {
      const r = await api.post<any>("/documents/ai-generate", { prompt: aiPrompt, doc_type: meta.doc_type, preset: preset || "" });
      if (r?.body_html) loadBody(r.body_html);
    } catch (e: any) { setErr(e?.message || "Could not build the document"); }
    finally { setAiBusy(false); }
  }

  const bodyNow = async () => cleanTemplate(await latestHtml(), sanitizeHtml);

  async function fetchPreview() {
    try { const r = await api.post<any>("/documents/preview", { body_html: await bodyNow(), doc_type: meta.doc_type, barcode_field: meta.barcode_field, barcode_types: meta.barcode_types }); setPreview(r?.html || ""); setSample(r?.sample || null); }
    catch { setPreview("<p style='color:#999'>Preview unavailable.</p>"); }
  }

  function insertVar(tok: string) {
    if (view === "html" && codeRef.current) {
      const ta = codeRef.current; const a = ta.selectionStart ?? html.length, b = ta.selectionEnd ?? a;
      setHtml(html.slice(0, a) + tok + html.slice(b));
      requestAnimationFrame(() => { ta.focus(); ta.setSelectionRange(a + tok.length, a + tok.length); });
      return;
    }
    if (insertRef.current) insertRef.current(tok);
  }

  // ── template list + in-place switching ──
  async function loadList() {
    try { const d = await api.get<any>("/documents/templates"); setTemplates(d?.items || []); } catch { /* keep prior */ }
  }
  function openTpl(t: any) {
    setActionsOpen(false);
    setMeta({ name: t.name || "", doc_type: t.doc_type || "estimate", page_size: t.page_size || "a4", font_family: t.font_family || "Inter", is_default: !!t.is_default, auto_print_on: t.auto_print_on || "", page_w_mm: t.page_w_mm ?? null, page_h_mm: t.page_h_mm ?? null, margin_mm: t.margin_mm ?? null, barcode_field: t.barcode_field ?? null, barcode_types: t.barcode_types || {} });
    setCurrentId(t.id || null);
    loadBody(t.body_html || "");
  }
  function newDoc() {
    setActionsOpen(false);
    setMeta({ name: "", doc_type: "estimate", page_size: "a4", font_family: "Inter", is_default: false, auto_print_on: "", page_w_mm: null, page_h_mm: null, margin_mm: null, barcode_field: null, barcode_types: {} });
    setCurrentId(null); setBlankOk(false);
    loadBody("");
  }

  async function save() {
    if (!meta.name.trim()) { setErr("Template name is required"); return; }
    setBusy(true); setErr(null);
    const payload = { ...meta, body_html: await bodyNow() };
    try {
      if (currentId) await api.put(`/documents/templates/${currentId}`, payload);
      else { const r = await api.post<any>("/documents/templates", payload); setCurrentId(r?.id || null); }
      say("Saved"); await loadList(); reload?.();
    } catch (e: any) { setErr(e?.message || "Could not save"); }
    finally { setBusy(false); }
  }
  async function duplicate() {
    setActionsOpen(false); setErr(null);
    try {
      const r = await api.post<any>("/documents/templates", { ...meta, name: (meta.name || "Template") + " (copy)", is_default: false, auto_print_on: "", body_html: await bodyNow() });   // a copy never double-prints
      say("Duplicated"); await loadList(); reload?.();
      if (r?.id) { setCurrentId(r.id); setMeta((m) => ({ ...m, name: (m.name || "Template") + " (copy)", is_default: false, auto_print_on: "" })); }
    } catch (e: any) { setErr(e?.message || "Could not duplicate"); }
  }
  async function setDefault() {
    setActionsOpen(false); if (!currentId) { setErr("Save the template first"); return; }
    try { await api.put(`/documents/templates/${currentId}`, { ...meta, is_default: true, body_html: await bodyNow() }); setMeta((m) => ({ ...m, is_default: true })); say("Set as default"); await loadList(); reload?.(); }
    catch (e: any) { setErr(e?.message || "Failed"); }
  }
  async function del() {
    setActionsOpen(false); if (!currentId) { newDoc(); return; }
    if (!(await confirmDialog("Delete this template?", { danger: true, confirmLabel: "Delete" }))) return;
    try {
      await api.del(`/documents/templates/${currentId}`); say("Deleted"); reload?.();
      const d = await api.get<any>("/documents/templates"); const items = d?.items || []; setTemplates(items);
      if (items[0]) openTpl(items[0]); else newDoc();
    } catch (e: any) { setErr(e?.message || "Could not delete"); }
  }
  async function printDoc() {
    try {
      const r = await api.post<any>("/documents/print-view", {
        body_html: await bodyNow(), doc_type: meta.doc_type, page_size: meta.page_size,
        page_w_mm: meta.page_w_mm || null, page_h_mm: meta.page_h_mm || null,
        margin_mm: meta.margin_mm ?? null, font_family: meta.font_family || "Inter",
      });
      const out = r?.html; if (!out) return;
      printHtmlInFrame(out);
    } catch (e: any) { setErr(e?.message || "Print failed"); }
  }

  // "Most used" sits on top, so the everyday fields are one click away.
  const varGroups = useMemo(() => {
    const all = new Set(groups.flatMap((g) => g.items));
    const most = { group: "Most used", items: MOST_USED.filter((t) => all.has(t)) };
    return most.items.length ? [most, ...groups] : groups;
  }, [groups]);
  const ql = q.trim().toLowerCase();
  const railGroups = useMemo(() => varGroups.map((g) => ({ ...g, items: ql ? g.items.filter((i) => i.toLowerCase().includes(ql)) : g.items })).filter((g) => g.items.length), [varGroups, ql]);

  // paper-true canvas: real physical size → px at 96dpi (1mm = 3.7795px)
  const MM = 3.7795;
  const PRESET_MM: Record<string, [number, number | null]> = { a4: [210, 297], letter: [215.9, 279.4], receipt80: [80, null], receipt58: [58, null] };
  function pageMM(): { w: number; h: number | null } {
    if (meta.page_size === "label" && meta.page_w_mm && meta.page_h_mm) return { w: +meta.page_w_mm, h: +meta.page_h_mm };
    if (PRESET_MM[meta.page_size]) { const [w, h] = PRESET_MM[meta.page_size]; return { w, h }; }
    return { w: 210, h: 297 };
  }
  const pm = pageMM();
  const page: PageBox = {
    w: Math.round(pm.w * MM), minH: pm.h ? Math.round(pm.h * MM) : 420,
    pad: Math.round(((meta.margin_mm ?? (meta.page_size === "a4" || meta.page_size === "letter" ? 10 : 4)) as number) * MM),
    font: meta.font_family || "Inter",
  };
  // The visual views lay out the sheet when they open; a new size reopens them.
  const viewKey = `${epoch}:${page.w}:${page.minH}:${page.pad}:${page.font}`;

  const greyed = (["design", "text"] as ModeId[]).filter((m) => reasons?.[m]);
  const empty = !html.trim();
  // What a visual view opens with (read when it mounts, so only worked out then).
  const viewHtml = useMemo(() => cleanTemplate(html, sanitizeHtml), [view, viewKey]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-slate-100">
      {/* top bar */}
      <div className="flex items-center gap-3 px-4 h-14 bg-white border-b border-slate-200 shrink-0">
        <button aria-label="Close" onClick={onClose} title="Close" className="w-9 h-9 rounded-lg hover:bg-slate-200/60 flex items-center justify-center text-ink-tertiary"><M n="arrow_back" c="text-lg" /></button>
        <button onClick={newDoc} className={buttonClass("secondary", "md", "shrink-0")}><M n="add" c="text-base" /> New</button>
        {templates.length > 0 && (
          <TeklySelect value={currentId || ""} onChange={(e) => { const t = templates.find((x) => x.id === e.target.value); if (t) openTpl(t); }}
            className="glass-input h-9 max-w-[180px] rounded-xl px-3 text-sm">
            <option value="" disabled>Switch template…</option>
            {templates.map((t) => <option key={t.id} value={t.id}>{t.name}{t.is_default ? " (default)" : ""}</option>)}
          </TeklySelect>
        )}
        <input value={meta.name} onChange={(e) => setMeta({ ...meta, name: e.target.value })} placeholder="Untitled template" aria-label="Template name" className="text-md font-semibold text-ink-primary bg-transparent border-0 focus:outline-none focus:bg-slate-100 rounded px-2 py-1 w-44" />
        <div className="relative">
          <button aria-label="Actions" onClick={() => setActionsOpen((o) => !o)} title="Actions" className="w-9 h-9 rounded-lg hover:bg-slate-200/60 flex items-center justify-center text-ink-tertiary"><M n="more_vert" c="text-lg" /></button>
          {actionsOpen && (
            <div className="absolute left-0 top-11 z-20 w-48 bg-white border border-slate-200 rounded-xl shadow-drawer p-1">
              <button onClick={duplicate} className="w-full flex items-center gap-2 px-2 py-1.5 rounded-md hover:bg-slate-100 text-left text-sm text-ink-secondary"><M n="content_copy" c="text-ink-tertiary" /> Duplicate</button>
              <button onClick={setDefault} className="w-full flex items-center gap-2 px-2 py-1.5 rounded-md hover:bg-slate-100 text-left text-sm text-ink-secondary"><M n="check_circle" c="text-ink-tertiary" /> Set as default</button>
              <button onClick={del} className="w-full flex items-center gap-2 px-2 py-1.5 rounded-md hover:bg-rose-50 text-left text-sm text-rose-600"><M n="delete" c="" /> Delete</button>
            </div>
          )}
        </div>
        <Tabs look="segmented" label="Editor view" className="mx-auto" value={(view || "design") as View}
          onChange={(v) => choose(v)}
          tabs={[
            { id: "design", label: "Design", icon: <M n="dashboard" c="text-sm" />, disabled: !reasons || !!reasons.design, title: reasons?.design || "Drag and drop blocks onto the page" },
            { id: "text", label: "Text", icon: <M n="format_color_text" c="text-sm" />, disabled: !reasons || !!reasons.text, title: reasons?.text || "Edit it like a document" },
            { id: "html", label: "HTML", icon: <M n="code" c="text-sm" />, title: "Edit the HTML source" },
            { id: "preview", label: "Preview", icon: <M n="visibility" c="text-sm" />, title: "See it filled in with a recent job" },
          ]} />
        <div className="relative">
          <button aria-label="Document settings" onClick={() => setSettings((s) => !s)} title="Document settings" className="w-9 h-9 rounded-lg hover:bg-slate-200/60 flex items-center justify-center text-ink-tertiary"><M n="tune" c="text-lg" /></button>
          {settings && (
            <div className="absolute right-0 top-11 z-40 w-64 bg-white border border-slate-200 rounded-xl shadow-drawer p-3 space-y-3">
              <div><label className="block text-sm font-medium text-ink-primary mb-1">Type</label>
                <TeklySelect value={meta.doc_type} onChange={(e) => setMeta({ ...meta, doc_type: e.target.value, auto_print_on: keepAllowed(meta.auto_print_on, e.target.value) })} className="glass-input w-full rounded-xl px-3 py-2 text-sm capitalize">{DOC_TYPES.map((t) => <option key={t} value={t}>{t.replace(/_/g, " ")}</option>)}</TeklySelect></div>
              <div><label className="block text-sm font-medium text-ink-primary mb-1">Paper / label size</label>
                <TeklySelect value={meta.page_size} onChange={(e) => setMeta({ ...meta, page_size: e.target.value })} className="glass-input w-full rounded-xl px-3 py-2 text-sm">
                  <option value="a4">A4 (210×297mm)</option>
                  <option value="letter">Letter (8.5×11in)</option>
                  <option value="receipt80">Receipt 80mm</option>
                  <option value="receipt58">Receipt 58mm</option>
                  <option value="label">Custom label (mm)…</option>
                </TeklySelect></div>
              {meta.page_size === "label" && (
                <div className="grid grid-cols-2 gap-2">
                  <div><label className="block text-sm font-medium text-ink-primary mb-1">Width mm</label>
                    <input type="number" value={meta.page_w_mm ?? ""} onChange={(e) => setMeta({ ...meta, page_w_mm: e.target.value ? +e.target.value : null })} placeholder="40" className="glass-input w-full rounded-xl px-3 py-2 text-sm" /></div>
                  <div><label className="block text-sm font-medium text-ink-primary mb-1">Height mm</label>
                    <input type="number" value={meta.page_h_mm ?? ""} onChange={(e) => setMeta({ ...meta, page_h_mm: e.target.value ? +e.target.value : null })} placeholder="30" className="glass-input w-full rounded-xl px-3 py-2 text-sm" /></div>
                </div>
              )}
              <div><label className="block text-sm font-medium text-ink-primary mb-1">Margin mm (blank = auto)</label>
                <input type="number" value={meta.margin_mm ?? ""} onChange={(e) => setMeta({ ...meta, margin_mm: e.target.value ? +e.target.value : null })} placeholder={(meta.page_size === "a4" || meta.page_size === "letter") ? "10" : "0"} className="glass-input w-full rounded-xl px-3 py-2 text-sm" /></div>
              <div><label className="block text-sm font-medium text-ink-primary mb-1">Default font</label>
                <TeklySelect value={meta.font_family} onChange={(e) => setMeta({ ...meta, font_family: e.target.value })} className="glass-input w-full rounded-xl px-3 py-2 text-sm">{FONTS.map((f) => <option key={f} value={f}>{f}</option>)}</TeklySelect></div>
              <div className="flex items-center gap-2 text-sm text-ink-secondary"><Switch size="sm" label="Default for this type" on={!!meta.is_default} onChange={(v) => setMeta({ ...meta, is_default: v })} /> Default for this type</div>
              {autoPrintChoices(meta.doc_type).length > 0 && (
                <fieldset><legend className="block text-sm font-medium text-ink-primary mb-1">Print automatically when</legend>
                  {autoPrintChoices(meta.doc_type).map(([ev, label]) => {
                    const on = (meta.auto_print_on || "").split(",").includes(ev);
                    return (
                      <label key={ev} className="flex items-center gap-2 text-sm text-ink-secondary">
                        <input type="checkbox" checked={on} onChange={() => setMeta({ ...meta, auto_print_on: toggleEvent(meta.auto_print_on, ev) })} /> {label}</label>
                    );
                  })}
                  <span className="block text-2xs text-ink-tertiary mt-1">{meta.doc_type === "label"
                    ? "One label per item on the job. Only labels that use {Asset …} variables can print by themselves."
                    : "Nothing ticked: it only prints when someone presses Print."}</span>
                </fieldset>
              )}
              {LABEL_DOC_TYPES.includes(meta.doc_type) && <LabelBarcodePicker value={meta} onChange={(v) => setMeta({ ...meta, ...v })} />}
            </div>
          )}
        </div>
        <button onClick={() => setShowSmart((s) => !s)} className={`h-9 px-3 rounded-lg border text-xs font-semibold flex items-center gap-1 ${showSmart ? "border-primary text-primary bg-primary/5" : "border-slate-200 text-ink-secondary hover:bg-slate-100"}`}><M n="bolt" c="text-base" /> Smart builder</button>
        <button onClick={() => setVars((r) => !r)} className={`h-9 px-3 rounded-lg border text-xs font-semibold flex items-center gap-1 ${vars ? "border-primary text-primary bg-primary/5" : "border-slate-200 text-ink-secondary hover:bg-slate-100"}`}><M n="data_object" c="text-base" /> Variables</button>
      </div>

      {/* why a view is greyed out — one line each */}
      {greyed.length > 0 && view !== "preview" && (
        <div className="px-4 py-1.5 bg-white border-b border-slate-200 flex flex-wrap gap-x-5 gap-y-0.5 shrink-0" role="note" aria-label="Unavailable views">
          {greyed.map((m) => (
            <span key={m} className="text-2xs text-ink-tertiary flex items-center gap-1" data-testid={`reason-${m}`}>
              <M n="info" c="text-xs" /><strong className="font-semibold text-ink-secondary">{MODE_NAME[m]} is unavailable for this template.</strong> {reasons?.[m]}
            </span>
          ))}
        </div>
      )}

      {/* Smart builder — Tekly's own document intelligence (offline presets + describe) */}
      {showSmart && (
        <div className="px-4 py-2 bg-primary/5 border-b border-slate-200 flex items-center gap-2 flex-wrap shrink-0">
          <span className="inline-flex items-center gap-1 text-2xs font-semibold text-primary"><M n="bolt" c="" /> Smart builder</span>
          {presets.map((p) => (
            <button key={p.key} onClick={() => buildDoc(p.key)} disabled={aiBusy} className="text-2xs px-2 py-1 rounded-full border border-slate-200 bg-white hover:border-primary hover:text-primary disabled:opacity-60">{p.label}</button>
          ))}
          <span className="w-px h-5 bg-slate-200 mx-0.5" />
          <input value={aiPrompt} onChange={(e) => setAiPrompt(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") buildDoc(); }}
            placeholder="…or describe one — e.g. “estimate with logo, parts table, deposit line and signature”"
            className="glass-input flex-1 min-w-[220px] rounded-xl px-3 py-2 text-sm" />
          <button onClick={() => buildDoc()} disabled={aiBusy || !aiPrompt.trim()} className={buttonClass("primary", "sm")}>{aiBusy ? "Building…" : "Build"}</button>
        </div>
      )}

      {/* body: the view | variables rail */}
      <div className="flex-1 min-h-0 flex relative">
        <div className="relative flex-1 min-w-0 flex flex-col">
          {view === null && <div className="flex-1 grid place-items-center text-xs text-ink-tertiary">Opening the editor…</div>}
          <Suspense fallback={<div className="flex-1 grid place-items-center text-xs text-ink-tertiary">Loading the editor…</div>}>
            {view === "design" && <DesignMode key={viewKey} html={viewHtml} onChange={setHtml} groups={varGroups} page={page} bindInsert={bindInsert} bindFlush={bindFlush} />}
            {view === "text" && <TextMode key={viewKey} html={viewHtml} onChange={setHtml} groups={varGroups} page={page} bindInsert={bindInsert} />}
          </Suspense>
          {view === "html" && (
            <div className="flex-1 min-h-0 flex flex-col bg-slate-900">
              <div className="px-4 py-1.5 text-2xs font-semibold text-slate-300 border-b border-slate-700 flex items-center gap-2">
                <M n="code" c="text-xs" /> HTML source. Changes show in Design and Text when you switch back.
                <span className="ml-auto"><VariableMenu groups={varGroups} onPick={insertVar} label="Insert variable" onDark /></span>
              </div>
              <textarea ref={codeRef} value={html} onChange={(e) => setHtml(e.target.value)} spellCheck={false} aria-label="HTML source"
                className="flex-1 w-full bg-slate-900 text-slate-100 font-mono text-xs leading-5 p-4 outline-none resize-none" />
            </div>
          )}
          {view === "preview" && (
            <div className="relative flex-1 min-h-0 overflow-auto">
              {sample && <div className="absolute top-3 left-1/2 -translate-x-1/2 text-2xs text-ink-tertiary bg-white/80 border border-slate-200 rounded-full px-3 py-1">Preview using {sample}</div>}
              <div className="min-h-full flex justify-center py-10 px-6">
                <div className="doc-canvas bg-paper shadow-lg text-sm text-paper-ink" style={{ width: page.w, maxWidth: "100%", minHeight: page.minH, padding: page.pad, fontFamily: `${page.font}, Arial, sans-serif`, lineHeight: 1.5 }}
                  dangerouslySetInnerHTML={{ __html: preview ? sanitizeHtml(preview) : "<p style='color:#999'>Loading preview…</p>" }} />
              </div>
            </div>
          )}
          {empty && !blankOk && view !== null && view !== "preview" && presets.length > 0 && (
            <div className="absolute top-24 left-1/2 -translate-x-1/2 z-[5] w-[420px] max-w-[90%] bg-white rounded-2xl border border-slate-200 shadow-clay-drawer p-5">
              <div className="text-sm font-semibold text-ink-primary">Start from a layout</div>
              <div className="text-2xs text-ink-tertiary mb-3">Pick one and change anything you like. Your logo and details fill in automatically.</div>
              <div className="space-y-1.5">
                {[...presets].sort((a, b) => Number(b.doc_type === meta.doc_type) - Number(a.doc_type === meta.doc_type)).map((p) => (
                  <button key={p.key} onClick={() => { setBlankOk(true); buildDoc(p.key); }} disabled={aiBusy}
                    className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-xl border text-left transition-colors disabled:opacity-60 ${p.doc_type === meta.doc_type ? "border-primary/40 bg-primary/5" : "border-slate-200 hover:border-slate-300"}`}>
                    <M n="description" c="text-base text-primary" />
                    <span className="text-sm text-ink-primary flex-1">{p.label}</span>
                    {p.doc_type === meta.doc_type && <span className="text-2xs font-semibold text-primary">Suggested</span>}
                  </button>
                ))}
              </div>
              <button onClick={() => setBlankOk(true)} className="mt-3 w-full text-center text-xs font-semibold text-ink-secondary hover:text-ink-primary py-1.5">Start with a blank page</button>
            </div>
          )}
        </div>

        {vars && (
          <aside className="w-[280px] bg-white border-l border-slate-200 flex flex-col shrink-0" aria-label="Variables">
            <div className="p-2.5 border-b border-slate-200 flex items-center gap-2">
              <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search variables…" aria-label="Search variables" className="flex-1 bg-white border border-slate-200 rounded-md text-xs px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-primary" />
              <button aria-label="Close variables" onClick={() => setVars(false)} className="w-7 h-7 rounded-md hover:bg-slate-100 flex items-center justify-center text-ink-tertiary"><M n="close" c="text-base" /></button>
            </div>
            <div className="flex-1 overflow-y-auto p-1.5 space-y-2">
              <p className="text-2xs text-ink-tertiary px-2 pt-1">Click a variable to put it into the page.</p>
              {railGroups.length === 0 && <div className="text-xs text-ink-tertiary px-2 py-3">No matches.</div>}
              {railGroups.map((g) => (
                <div key={g.group}>
                  <div className="px-2 py-1 text-2xs font-semibold text-ink-secondary">{g.group}</div>
                  {g.items.map((tok) => (
                    <button key={tok} type="button" title={`Insert ${tok}`} disabled={view === "preview"} onMouseDown={(e) => { e.preventDefault(); insertVar(tok); }}
                      className="w-full flex items-center gap-1.5 pl-3 pr-2 py-1 text-xs text-primary text-left rounded hover:bg-primary/5 disabled:opacity-50"><span className="truncate">{tok.replace(/^\{|\}$/g, "")}</span></button>
                  ))}
                </div>
              ))}
            </div>
          </aside>
        )}
      </div>

      {/* footer: main action first, on the left (owner rule) */}
      <div className="flex items-center gap-2 px-4 h-14 bg-white border-t border-slate-200 shrink-0">
        <button onClick={save} disabled={busy} className={buttonClass("primary", "md")}>{busy ? "Saving…" : "Save"}</button>
        <button onClick={printDoc} className={buttonClass("secondary", "md")}><M n="print" c="text-base" /> Print at exact size</button>
        {flash && <span className="text-xs text-emerald-600 font-semibold" role="status">{flash}</span>}
        {err && <span className="ml-auto text-xs text-rose-700" role="alert">{err}</span>}
      </div>
    </div>
  );
}
