import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ArrowLeft, Code, Copy, CheckCircle2, Info, MoreHorizontal, Plus, Printer, SlidersHorizontal, Trash2, Undo2, Wand2 } from "lucide-react";
import TeklySelect from "../../components/TeklySelect";
import Tabs from "../../components/Tabs";
import { buttonClass } from "../../components/Button";
import LabelBarcodePicker, { LABEL_DOC_TYPES } from "./LabelBarcodePicker";
import { M } from "../../components/Shell";
import { api } from "../../api/client";
import { confirmDialog } from "../../lib/dialog";
import Switch from "../../components/Switch";
import { printHtmlInFrame, sanitizeHtml } from "../../lib/safeHtml";
import { allReasons, modeReason } from "./templateEditor/modeSupport";
import { cleanTemplate, placeholders, type ModeId } from "./templateEditor/templateHtml";
import FieldsPanel, { type VarGroup } from "./templateEditor/FieldsPanel";
import LayoutGallery from "./templateEditor/LayoutGallery";
import { proposeFix, type Fix } from "./templateEditor/autoFix";
import { fetchFieldValues, fieldDisplay, type FieldValues } from "./templateEditor/fieldValues";
import type { PageBox } from "./templateEditor/types";

/* Full-screen document template editor. One stored HTML template, edited in
     Design — drag-and-drop page builder (GrapesJS, loaded on demand)
     Text   — word-processor editing (Tiptap, loaded on demand)
     Code   — the HTML itself (from the More menu)
   plus Preview. Fields show the shop's real data unless "Show fields" is on (display only).
   A view that would change a template's markup is greyed out with the reason, and offers a safe
   rewrite when one exists. Changes save by themselves; an untouched template saves exactly as
   it was loaded. */

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
const PAPER: [string, string][] = [["a4", "A4"], ["letter", "Letter"], ["receipt80", "80 mm receipt"], ["receipt58", "58 mm receipt"], ["label", "Label"]];
export const AUTOSAVE_MS = 2000;

type View = ModeId | "preview";
const MODE_NAME: Record<ModeId, string> = { design: "Design", text: "Text", html: "Code" };
type Reasons = Record<ModeId, string | null> | null;
type Meta = {
  name: string; doc_type: string; page_size: string; font_family: string; is_default: boolean; auto_print_on: string;
  page_w_mm: number | null; page_h_mm: number | null; margin_mm: number | null; barcode_field: string | null; barcode_types: Record<string, string>;
};
const metaOf = (t: any): Meta => ({
  name: t?.name || "", doc_type: t?.doc_type || "estimate", page_size: t?.page_size || "a4", font_family: t?.font_family || "Inter",
  is_default: !!t?.is_default, auto_print_on: t?.auto_print_on || "", page_w_mm: t?.page_w_mm ?? null, page_h_mm: t?.page_h_mm ?? null,
  margin_mm: t?.margin_mm ?? null, barcode_field: t?.barcode_field ?? null, barcode_types: t?.barcode_types || {},
});

/** Who is signed in (the token's subject), so the last view used is remembered per person. */
function userKey(): string {
  try {
    const tok = localStorage.getItem("ros_tok") || "";
    const sub = JSON.parse(atob((tok.split(".")[1] || "").replace(/-/g, "+").replace(/_/g, "/")) || "{}").sub;
    return `tk_template_view:${sub || "me"}`;
  } catch { return "tk_template_view:me"; }
}
function readPref(): string | null { try { return localStorage.getItem(userKey()); } catch { return null; } }
function writePref(v: string) { try { localStorage.setItem(userKey(), v); } catch { /* private window */ } }

/** The view to open a template in: the last visual view used if it can hold this template,
 *  else Design, then Text, then the code. */
export function pickView(reasons: Record<ModeId, string | null>, preferred: string | null): ModeId {
  const order: ModeId[] = ["design", "text", "html"];
  if ((preferred === "design" || preferred === "text") && !reasons[preferred]) return preferred;
  return order.find((m) => !reasons[m]) || "html";
}

export default function TemplateEditorDrawer({ initial, onClose, reload }: {
  initial: any; onClose: () => void; reload?: () => void;
}) {
  const [meta, setMeta] = useState<Meta>(() => metaOf(initial));
  // The template body, exactly as stored. Every view reads and writes this one string.
  const [html, setHtml] = useState<string>(initial?.body_html || "");
  const [epoch, setEpoch] = useState(0);             // bump to reload the visual views from `html`
  const [view, setView] = useState<View | null>(null);
  const [reasons, setReasons] = useState<Reasons>(null);
  const [fixes, setFixes] = useState<Partial<Record<ModeId, Fix>>>({});
  const [fixing, setFixing] = useState<{ mode: ModeId; fix: Fix } | null>(null);
  const [templates, setTemplates] = useState<any[]>([]);
  const [currentId, setCurrentId] = useState<string | null>(initial?.id || null);
  const [menu, setMenu] = useState(false);
  const [settings, setSettings] = useState(false);
  const [groups, setGroups] = useState<VarGroup[]>([]);
  const [showFields, setShowFields] = useState(false);
  const [fieldData, setFieldData] = useState<FieldValues | null>(null);
  const [fieldsReady, setFieldsReady] = useState(false);
  const [gallery, setGallery] = useState(!initial?.id && !initial?.body_html);
  const [preview, setPreview] = useState("");
  const [sample, setSample] = useState<string | null>(null);
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [err, setErr] = useState<string | null>(null);
  const [leaving, setLeaving] = useState<null | (() => void)>(null);   // unsaved-changes prompt
  // What was last saved (or loaded) and what was opened (for "Undo all changes").
  const saved = useRef({ html: initial?.body_html || "", meta: JSON.stringify(metaOf(initial)) });
  const opened = useRef({ html: initial?.body_html || "", meta: metaOf(initial) });
  const insertRef = useRef<((tok: string) => void) | null>(null);
  const bindInsert = useCallback((fn: ((tok: string) => void) | null) => { insertRef.current = fn; }, []);
  const flushRef = useRef<(() => Promise<string | null>) | null>(null);
  const bindFlush = useCallback((fn: (() => Promise<string | null>) | null) => { flushRef.current = fn; }, []);
  const codeRef = useRef<HTMLTextAreaElement | null>(null);
  const busy = saveState === "saving";
  const metaKey = JSON.stringify(meta);
  const dirty = html !== saved.current.html || metaKey !== saved.current.meta;

  useEffect(() => {
    api.get<any>("/documents/templates").then((d) => { setGroups(d?.variable_groups || []); setTemplates(d?.items || []); }).catch(() => setGroups([]));
  }, []);

  // Real values for every field, from the shop's latest record of this kind (display only).
  useEffect(() => {
    if (!groups.length) return;
    let on = true;
    const tokens = [...groups.flatMap((g) => g.items), ...placeholders(html)];
    fetchFieldValues((p, b) => api.post<any>(p, b), tokens, meta.doc_type)
      .then((d) => { if (on) { setFieldData(d); setFieldsReady(true); } })
      .catch(() => { if (on) { setFieldData({ values: {}, sample: null }); setFieldsReady(true); } });
    return () => { on = false; };
  }, [groups, meta.doc_type]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { const t = setTimeout(() => setFieldsReady(true), 4000); return () => clearTimeout(t); }, []);

  // Which views can hold this template, and a safe rewrite for the ones that can't.
  useEffect(() => {
    let live = true;
    const t = setTimeout(async () => {
      const clean = cleanTemplate(html, sanitizeHtml);
      let r: Record<ModeId, string | null>;
      try { r = await allReasons(clean); } catch { r = { design: null, text: null, html: null }; }
      const offer: Partial<Record<ModeId, Fix>> = {};
      const fix = (r.design || r.text) ? proposeFix(clean) : null;
      if (fix) for (const m of ["design", "text"] as const) if (r[m] && !(await modeReason(m, fix.html).catch(() => "x"))) offer[m] = fix;
      if (!live) return;
      setReasons(r); setFixes(offer);
      setView((v) => v ?? pickView(r, readPref()));
    }, view ? 500 : 0);
    return () => { live = false; clearTimeout(t); };
  }, [html, epoch]); // eslint-disable-line react-hooks/exhaustive-deps

  /** The template right now, including text still being typed in the Design canvas. */
  async function latestHtml(): Promise<string> {
    const out = await flushRef.current?.();
    if (out != null && out !== html) { setHtml(out); return out; }
    return html;
  }
  async function choose(v: View) {
    await latestHtml();
    if (v === "design" || v === "text") writePref(v);
    if (v === "preview") fetchPreview();
    setView(v);
  }
  /** Replace the whole body from outside the views (open a template, undo all). */
  function loadBody(next: string) { setHtml(next); setReasons(null); setView(null); setEpoch((e) => e + 1); }

  async function fetchPreview() {
    try { const r = await api.post<any>("/documents/preview", { body_html: cleanTemplate(await latestHtml(), sanitizeHtml), doc_type: meta.doc_type, barcode_field: meta.barcode_field, barcode_types: meta.barcode_types }); setPreview(r?.html || ""); setSample(r?.sample || null); }
    catch { setPreview("<p style='color:#999'>Preview unavailable.</p>"); }
  }

  function insertVar(tok: string) {
    if (view === "html" && codeRef.current) {
      const ta = codeRef.current; const a = ta.selectionStart ?? html.length, b = ta.selectionEnd ?? a;
      setHtml(html.slice(0, a) + tok + html.slice(b));
      requestAnimationFrame(() => { ta.focus(); ta.setSelectionRange(a + tok.length, a + tok.length); });
      return;
    }
    insertRef.current?.(tok);
  }

  // ── saving ──
  async function save(quiet = false): Promise<boolean> {
    if (!meta.name.trim()) { if (!quiet) setErr("Give the template a name to save it"); return false; }
    setSaveState("saving"); setErr(null);
    const raw = await latestHtml();
    const metaNow = JSON.stringify(meta);
    const payload = { ...meta, body_html: cleanTemplate(raw, sanitizeHtml) };
    try {
      if (currentId) await api.put(`/documents/templates/${currentId}`, payload);
      else { const r = await api.post<any>("/documents/templates", payload); setCurrentId(r?.id || null); }
      saved.current = { html: raw, meta: metaNow };
      setSaveState("saved");
      if (!quiet) { loadList(); reload?.(); }
      return true;
    } catch (e: any) { setSaveState("error"); setErr(e?.message || "Could not save"); return false; }
  }
  const saveRef = useRef(save); saveRef.current = save;
  // Autosave: about two seconds after the last change, and only when something changed.
  useEffect(() => {
    if (!dirty || !currentId || !meta.name.trim() || busy) return;
    const t = setTimeout(() => { saveRef.current(true).then((ok) => { if (ok) reload?.(); }); }, AUTOSAVE_MS);
    return () => clearTimeout(t);
  }, [html, metaKey, currentId, busy]); // eslint-disable-line react-hooks/exhaustive-deps
  // Ctrl/Cmd+S saves now; leaving the page with unsaved changes asks first.
  useEffect(() => {
    const key = (e: KeyboardEvent) => { if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") { e.preventDefault(); saveRef.current(false); } };
    window.addEventListener("keydown", key, true);
    return () => window.removeEventListener("keydown", key, true);
  }, []);
  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  /** Run `next` once the current changes are safe: saved if they can be, else ask. */
  async function guard(next: () => void) {
    const latest = await latestHtml();
    const isDirty = latest !== saved.current.html || JSON.stringify(meta) !== saved.current.meta;
    if (!isDirty) { next(); return; }
    setLeaving(() => next);
  }
  const close = () => guard(onClose);

  async function loadList() {
    try { const d = await api.get<any>("/documents/templates"); setTemplates(d?.items || []); } catch { /* keep prior */ }
  }
  function openTpl(t: any) {
    setMenu(false);
    const m = metaOf(t);
    setMeta(m); setCurrentId(t.id || null); setSaveState("idle"); setErr(null);
    saved.current = { html: t.body_html || "", meta: JSON.stringify(m) };
    opened.current = { html: t.body_html || "", meta: m };
    loadBody(t.body_html || "");
  }
  function blankDoc() {
    setGallery(false);
    openTpl({ doc_type: meta.doc_type || "estimate", name: "", body_html: "" });
  }
  function undoAll() {
    setMenu(false);
    confirmDialog("Put this template back the way it was when you opened it? Your changes since then are dropped.", { title: "Undo all changes", confirmLabel: "Undo all changes" })
      .then((ok) => { if (ok) { setMeta(opened.current.meta); loadBody(opened.current.html); } });
  }
  async function duplicate() {
    setMenu(false); setErr(null);
    try {
      const body = cleanTemplate(await latestHtml(), sanitizeHtml);
      const r = await api.post<any>("/documents/templates", { ...meta, name: (meta.name || "Template") + " (copy)", is_default: false, auto_print_on: "", body_html: body });   // a copy never double-prints
      await loadList(); reload?.();
      if (r?.id) openTpl({ ...meta, id: r.id, name: (meta.name || "Template") + " (copy)", is_default: false, auto_print_on: "", body_html: body });
    } catch (e: any) { setErr(e?.message || "Could not duplicate"); }
  }
  async function setDefault() {
    setMenu(false); if (!currentId) { setErr("Save the template first"); return; }
    setMeta((m) => ({ ...m, is_default: true }));   // autosave stores it
  }
  async function del() {
    setMenu(false); if (!currentId) { blankDoc(); return; }
    if (!(await confirmDialog("Delete this template?", { danger: true, confirmLabel: "Delete" }))) return;
    try {
      await api.del(`/documents/templates/${currentId}`); reload?.();
      const d = await api.get<any>("/documents/templates"); const items = d?.items || []; setTemplates(items);
      saved.current = { html, meta: metaKey };
      if (items[0]) openTpl(items[0]); else blankDoc();
    } catch (e: any) { setErr(e?.message || "Could not delete"); }
  }
  async function printDoc() {
    try {
      const r = await api.post<any>("/documents/print-view", {
        body_html: cleanTemplate(await latestHtml(), sanitizeHtml), doc_type: meta.doc_type, page_size: meta.page_size,
        page_w_mm: meta.page_w_mm || null, page_h_mm: meta.page_h_mm || null,
        margin_mm: meta.margin_mm ?? null, font_family: meta.font_family || "Inter",
      });
      const out = r?.html; if (!out) return;
      printHtmlInFrame(out);
    } catch (e: any) { setErr(e?.message || "Print failed"); }
  }
  async function applyFix() {
    if (!fixing) return;
    const { mode, fix } = fixing;
    setFixing(null);
    setHtml(fix.html);
    const r = await allReasons(cleanTemplate(fix.html, sanitizeHtml)).catch(() => null);
    if (r) { setReasons(r); setFixes({}); if (!r[mode]) setView(mode); }
  }

  // "Most used" first, so the everyday fields are one click away.
  const varGroups = useMemo(() => {
    const all = new Set(groups.flatMap((g) => g.items));
    const most = { group: "Most used", items: MOST_USED.filter((t) => all.has(t)) };
    return most.items.length ? [most, ...groups] : groups;
  }, [groups]);

  // paper-true canvas: real physical size → px at 96dpi (1mm = 3.7795px)
  const MM = 3.7795;
  const PRESET_MM: Record<string, [number, number | null]> = { a4: [210, 297], letter: [215.9, 279.4], receipt80: [80, null], receipt58: [58, null] };
  function pageMM(): { w: number; h: number | null } {
    if (meta.page_size === "label" && meta.page_w_mm && meta.page_h_mm) return { w: +meta.page_w_mm, h: +meta.page_h_mm };
    if (PRESET_MM[meta.page_size]) { const [w, h] = PRESET_MM[meta.page_size]; return { w, h }; }
    return { w: 210, h: 297 };
  }
  const pm = pageMM();
  const pageH = pm.h ? Math.round(pm.h * MM) : null;
  const page: PageBox = {
    w: Math.round(pm.w * MM), h: pageH, minH: pageH ?? 420,
    pad: Math.round(((meta.margin_mm ?? (meta.page_size === "a4" || meta.page_size === "letter" ? 10 : 4)) as number) * MM),
    font: meta.font_family || "Inter",
  };
  const display = useCallback((tok: string) => fieldDisplay(tok, fieldData, showFields, sanitizeHtml), [fieldData, showFields]);
  // The visual views lay out the sheet and fields when they open; a change reopens them.
  const viewKey = `${epoch}:${page.w}:${page.minH}:${page.pad}:${page.font}:${showFields}:${!!fieldData}`;
  // What a visual view opens with (read when it mounts, so only worked out then).
  const viewHtml = useMemo(() => cleanTemplate(html, sanitizeHtml), [view, viewKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const greyed = (["design", "text"] as ModeId[]).filter((m) => reasons?.[m]);
  const status = saveState === "saving" ? "Saving…" : saveState === "error" ? "Not saved" : dirty ? (currentId ? "Unsaved changes" : "Not saved yet") : currentId ? "All changes saved" : "";
  const ready = view !== null && fieldsReady;
  const paperOptions = PAPER.filter(([k]) => ["a4", "letter", "receipt80"].includes(k) || k === meta.page_size);

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-slate-100">
      {/* top bar */}
      <div className="flex items-center gap-2 px-3 h-14 bg-white border-b border-slate-200 shrink-0">
        <button onClick={close} className={buttonClass("ghost", "md")}><ArrowLeft size={16} aria-hidden="true" /> Close</button>
        <button onClick={() => guard(() => setGallery(true))} className={buttonClass("secondary", "md", "shrink-0")}><Plus size={16} aria-hidden="true" /> New</button>
        {templates.length > 0 && (
          <TeklySelect value={currentId || ""} aria-label="Open another template" onChange={(e) => { const t = templates.find((x) => x.id === e.target.value); if (t) guard(() => openTpl(t)); }}
            className="glass-input h-9 max-w-[170px] rounded-xl px-3 text-sm">
            <option value="" disabled>Open template…</option>
            {templates.map((t) => <option key={t.id} value={t.id}>{t.name}{t.is_default ? " (default)" : ""}</option>)}
          </TeklySelect>
        )}
        <input value={meta.name} onChange={(e) => setMeta({ ...meta, name: e.target.value })} placeholder="Untitled template" aria-label="Template name" className="text-md font-semibold text-ink-primary bg-transparent border-0 focus:outline-none focus:bg-slate-100 rounded px-2 py-1 w-40" />
        <div className="relative">
          <button onClick={() => setMenu((o) => !o)} aria-haspopup="menu" aria-expanded={menu} className={buttonClass("ghost", "md")}><MoreHorizontal size={16} aria-hidden="true" /> More</button>
          {menu && (
            <div role="menu" className="absolute left-0 top-11 z-40 w-52 bg-white border border-slate-200 rounded-xl shadow-drawer p-1">
              <MenuItem icon={<Code size={15} />} label="Edit code" onClick={() => { setMenu(false); choose("html"); }} />
              <MenuItem icon={<Undo2 size={15} />} label="Undo all changes" disabled={html === opened.current.html && metaKey === JSON.stringify(opened.current.meta)} onClick={undoAll} />
              <div className="h-px bg-slate-100 my-1" />
              <MenuItem icon={<Copy size={15} />} label="Duplicate" onClick={duplicate} />
              <MenuItem icon={<CheckCircle2 size={15} />} label="Use by default" onClick={setDefault} />
              <MenuItem icon={<Trash2 size={15} />} label="Delete" danger onClick={del} />
            </div>
          )}
        </div>
        <Tabs look="segmented" label="Editor view" className="mx-auto" value={(view || "design") as View}
          onChange={(v) => choose(v)}
          tabs={[
            { id: "design", label: "Design", icon: <M n="dashboard" c="text-sm" />, disabled: !reasons || !!reasons.design, title: reasons?.design || "Drag and drop blocks onto the page" },
            { id: "text", label: "Text", icon: <M n="format_color_text" c="text-sm" />, disabled: !reasons || !!reasons.text, title: reasons?.text || "Edit it like a document" },
            { id: "preview", label: "Preview", icon: <M n="visibility" c="text-sm" />, title: "See it exactly as it prints" },
          ]} />
        <label className="flex items-center gap-1.5 text-xs font-semibold text-ink-secondary">Paper
          <TeklySelect value={meta.page_size} aria-label="Paper size" onChange={(e) => setMeta({ ...meta, page_size: e.target.value })} className="glass-input h-9 rounded-xl px-2.5 text-sm">
            {paperOptions.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </TeklySelect>
        </label>
        <div className="relative">
          <button onClick={() => setSettings((s) => !s)} aria-expanded={settings} className={buttonClass("ghost", "md")}><SlidersHorizontal size={16} aria-hidden="true" /> Page &amp; print</button>
          {settings && (
            <div className="absolute right-0 top-11 z-40 w-64 bg-white border border-slate-200 rounded-xl shadow-drawer p-3 space-y-3">
              <div><label className="block text-sm font-medium text-ink-primary mb-1">Type</label>
                <TeklySelect value={meta.doc_type} onChange={(e) => setMeta({ ...meta, doc_type: e.target.value, auto_print_on: keepAllowed(meta.auto_print_on, e.target.value) })} className="glass-input w-full rounded-xl px-3 py-2 text-sm capitalize">{DOC_TYPES.map((t) => <option key={t} value={t}>{t.replace(/_/g, " ")}</option>)}</TeklySelect></div>
              <div><label className="block text-sm font-medium text-ink-primary mb-1">Paper</label>
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
        <label className="flex items-center gap-2 text-xs font-semibold text-ink-secondary pl-1" title="Show field names instead of your shop's data">
          <Switch size="sm" label="Show fields" on={showFields} onChange={setShowFields} /> Show fields
        </label>
      </div>

      {/* why a view is greyed out, one line each, with a safe fix when there is one */}
      {greyed.length > 0 && view !== "preview" && (
        <div className="px-4 py-1.5 bg-white border-b border-slate-200 flex flex-col gap-1 shrink-0" role="note" aria-label="Unavailable views">
          {greyed.map((m) => (
            <span key={m} className="text-2xs text-ink-tertiary flex items-center gap-1.5 flex-wrap" data-testid={`reason-${m}`}>
              <Info size={13} aria-hidden="true" /><strong className="font-semibold text-ink-secondary">{MODE_NAME[m]} is unavailable for this template.</strong> {reasons?.[m]}
              {fixes[m] && greyed.find((g) => fixes[g]) === m && <button type="button" onClick={() => setFixing({ mode: m, fix: fixes[m]! })} className={buttonClass("secondary", "sm", "ml-1")}><Wand2 size={13} aria-hidden="true" /> Make this editable visually</button>}
            </span>
          ))}
        </div>
      )}

      {/* body */}
      <div className="flex-1 min-h-0 flex relative">
        <div className="relative flex-1 min-w-0 flex flex-col">
          {!ready && <div className="flex-1 grid place-items-center text-xs text-ink-tertiary">Opening the editor…</div>}
          <Suspense fallback={<div className="flex-1 grid place-items-center text-xs text-ink-tertiary">Loading the editor…</div>}>
            {ready && view === "design" && <DesignMode key={viewKey} html={viewHtml} onChange={setHtml} groups={varGroups} page={page} display={display} bindInsert={bindInsert} bindFlush={bindFlush} onSaveShortcut={() => saveRef.current(false)} />}
            {ready && view === "text" && <TextMode key={viewKey} html={viewHtml} onChange={setHtml} groups={varGroups} page={page} display={display} bindInsert={bindInsert} />}
          </Suspense>
          {ready && view === "html" && (
            <div className="flex-1 min-h-0 flex">
              <aside className="w-64 shrink-0 bg-white border-r border-slate-200 overflow-y-auto" aria-label="Fields list"><FieldsPanel groups={varGroups} onPick={insertVar} /></aside>
              <div className="flex-1 min-w-0 flex flex-col bg-slate-900">
                <div className="px-4 py-1.5 text-2xs font-semibold text-slate-300 border-b border-slate-700 flex items-center gap-2">
                  <Code size={13} aria-hidden="true" /> Code. Changes show in Design and Text when you go back.
                  <button type="button" onClick={() => choose(pickView(reasons || { design: null, text: null, html: null }, readPref()))} className="ml-auto h-7 px-2.5 rounded-lg text-2xs font-semibold text-slate-100 hover:bg-white/10">Back to the page</button>
                </div>
                <textarea ref={codeRef} value={html} onChange={(e) => setHtml(e.target.value)} spellCheck={false} aria-label="HTML source"
                  className="flex-1 w-full bg-slate-900 text-slate-100 font-mono text-xs leading-5 p-4 outline-none resize-none" />
              </div>
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
        </div>
      </div>

      {/* footer: main action first, on the left (owner rule) */}
      <div className="flex items-center gap-2 px-4 h-14 bg-white border-t border-slate-200 shrink-0">
        <button onClick={() => save(false)} disabled={busy} className={buttonClass("primary", "md")}>{busy ? "Saving…" : "Save"}</button>
        <button onClick={printDoc} className={buttonClass("secondary", "md")}><Printer size={15} aria-hidden="true" /> Print at exact size</button>
        <span className="text-xs text-ink-tertiary" role="status" data-testid="save-status">{status}{status && currentId ? " · saves by itself" : ""}</span>
        {fieldData && !showFields && view !== "html" && view !== "preview" && (
          <span className="ml-auto text-2xs text-ink-tertiary">{fieldData.sample ? `Fields show ${fieldData.sample}. ` : "No records yet: fields show sample values. "}Turn on Show fields to see their names.</span>
        )}
        {err && <span className={`${fieldData ? "" : "ml-auto"} text-xs text-rose-700`} role="alert">{err}</span>}
      </div>

      {gallery && (
        <LayoutGallery api={{ get: (p) => api.get<any>(p), post: (p, b) => api.post<any>(p, b) }} templates={templates}
          onClose={() => setGallery(false)} onBlank={blankDoc}
          onCreated={(t) => { setGallery(false); setTemplates((l) => [...l, t]); reload?.(); openTpl(t); }} />
      )}

      {fixing && (
        <div className="fixed inset-0 z-[70] flex items-center justify-center p-6" role="dialog" aria-modal="true" aria-label="Make this editable visually">
          <div className="absolute inset-0 bg-ink-primary/40" onClick={() => setFixing(null)} aria-hidden="true" />
          <div className="relative w-full max-w-2xl max-h-full overflow-y-auto bg-white rounded-2xl border border-slate-200 shadow-clay-drawer p-5 space-y-3">
            <h2 className="text-sm font-semibold text-ink-primary">Make this template editable in {MODE_NAME[fixing.mode]}</h2>
            <p className="text-xs text-ink-secondary">These changes rewrite the template so the visual views can hold it. It prints the same. Nothing is saved until you confirm.</p>
            <ol className="space-y-2.5">
              {fixing.fix.changes.map((c, i) => (
                <li key={i} className="text-xs">
                  <div className="font-semibold text-ink-primary mb-1">{i + 1}. {c.what}</div>
                  {c.before && c.before !== c.after && <pre className="whitespace-pre-wrap break-all font-mono text-2xs rounded-lg p-2 bg-rose-50 text-rose-800 border border-rose-100" aria-label="Before">{c.before.slice(0, 400)}</pre>}
                  <pre className="whitespace-pre-wrap break-all font-mono text-2xs rounded-lg p-2 bg-emerald-50 text-emerald-800 border border-emerald-100" aria-label="After">{c.after.slice(0, 400)}</pre>
                </li>
              ))}
            </ol>
            <div className="flex gap-2 pt-1">
              <button type="button" onClick={applyFix} className={buttonClass("primary", "md")}>Make editable</button>
              <button type="button" onClick={() => setFixing(null)} className={buttonClass("secondary", "md")}>Cancel</button>
            </div>
          </div>
        </div>
      )}

      {leaving && (
        <div className="fixed inset-0 z-[70] flex items-center justify-center p-6" role="dialog" aria-modal="true" aria-label="Unsaved changes">
          <div className="absolute inset-0 bg-ink-primary/40" onClick={() => setLeaving(null)} aria-hidden="true" />
          <div className="relative w-full max-w-sm bg-white rounded-2xl border border-slate-200 shadow-clay-drawer p-5 space-y-3">
            <h2 className="text-sm font-semibold text-ink-primary">Unsaved changes</h2>
            <p className="text-xs text-ink-secondary">{currentId ? "Your last changes haven't been saved yet." : "This template hasn't been saved yet."}</p>
            <div className="flex flex-wrap gap-2">
              <button type="button" onClick={async () => { const next = leaving; if (await save(false)) { setLeaving(null); next(); } }} className={buttonClass("primary", "md")}>Save and continue</button>
              <button type="button" onClick={() => { const next = leaving; setLeaving(null); next(); }} className={buttonClass("secondary", "md")}>Don't save</button>
              <button type="button" onClick={() => setLeaving(null)} className={buttonClass("ghost", "md")}>Keep editing</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function MenuItem({ icon, label, onClick, danger, disabled }: { icon: ReactNode; label: string; onClick: () => void; danger?: boolean; disabled?: boolean }) {
  return (
    <button role="menuitem" type="button" onClick={onClick} disabled={disabled}
      className={`w-full flex items-center gap-2 px-2 py-1.5 rounded-md text-left text-sm disabled:opacity-40 ${danger ? "text-rose-600 hover:bg-rose-50" : "text-ink-secondary hover:bg-slate-100"}`}>
      <span className={danger ? "" : "text-ink-tertiary"}>{icon}</span>{label}
    </button>
  );
}
