import { useEffect, useMemo, useRef, useState } from "react";
import { M } from "../../components/Shell";
import { api } from "../../api/client";
import { confirmDialog } from "../../lib/dialog";
import { escapeHtml, printHtmlInFrame, sanitizeHtml } from "../../lib/safeHtml";

/* 144hr — Full-screen visual document editor: Edit⇄Preview, rich text (font family /
   size / colour), a left Blocks panel (add / reorder / remove sections), and a right
   variables slide-over. Pills + blocks serialize to plain HTML + {Tokens} on save. */

type Grp = { group: string; items: string[] };
const MOST_USED = ["{Company Name}", "{Company Logo}", "{Company Phone}", "{Client Name}", "{Client Phone}", "{Order Number}",
  "{Order Created Date}", "{Asset Brand}", "{Asset Model}", "{Asset Serial Number}", "{Order Complaint}", "{Items Table}",
  "{All Items Table}", "{Order Total}", "{Order Balance Due}", "{Barcode}"];
const DOC_TYPES = ["estimate", "invoice", "receipt", "work_order", "label", "other"];
// small identifying icon per variable group (Material Symbols names)
const GROUP_ICON: Record<string, string> = {
  "Most used": "auto_awesome", Company: "storefront", "Legal entity": "gavel", Interactive: "bolt", Client: "person",
  Payer: "account_balance_wallet", Ticket: "receipt_long", Device: "devices", Tables: "table_rows",
  Employees: "badge", Finance: "payments", Invoice: "description", Date: "calendar_today",
  Links: "link", Other: "more_horiz", "Custom fields": "tune",
};
const FONTS = ["Inter", "Arial", "Helvetica", "Georgia", "Times New Roman", "Courier New", "Verdana", "Tahoma", "Roboto", "Trebuchet MS"];
const SIZES = [10, 11, 12, 14, 16, 18, 20, 24, 28, 32, 40, 48];

function pillHTML(tok: string) {
  const label = tok.replace(/^\{|\}$/g, "");
  return `<span class="tok-pill" contenteditable="false" data-tok="${escapeHtml(tok)}">${escapeHtml(label)}</span>`;
}
function hydrate(html: string) {
  // Saved templates go into the live editor DOM: strip anything that could run script first.
  return sanitizeHtml(html || "").replace(/\{[A-Z][^{}]*\}/g, (m) => pillHTML(m));
}
function serialize(root: HTMLElement) {
  const clone = root.cloneNode(true) as HTMLElement;
  clone.querySelectorAll(".tok-pill").forEach((el) => el.replaceWith(document.createTextNode((el as HTMLElement).dataset.tok || "")));
  clone.querySelectorAll("[contenteditable]").forEach((el) => el.removeAttribute("contenteditable"));
  clone.querySelectorAll("[data-bid]").forEach((el) => el.removeAttribute("data-bid"));
  return clone.innerHTML;
}
function itemsTableHTML() {
  const c = (s: string) => `<td style="border:1px solid #ddd;padding:6px">${s}</td>`;
  return `<table style="width:100%;border-collapse:collapse;margin:8px 0"><thead><tr>${["#", "Item", "Qty", "Price", "Amount"].map((h) => `<th style="border:1px solid #ddd;padding:6px;text-align:left;background:#f5f5f5">${h}</th>`).join("")}</tr></thead><tbody><tr>${c(pillHTML("{Row Number}"))}${c(pillHTML("{Product Name}"))}${c(pillHTML("{Product Qty}"))}${c(pillHTML("{Product Price}"))}${c(pillHTML("{Product Amount}"))}</tr></tbody></table>`;
}

// Prebuilt document sections for the Blocks panel.
const BLOCKS: { key: string; label: string; icon: string; html: () => string }[] = [
  { key: "header", label: "Header", icon: "view_headline", html: () => `<div style="display:flex;justify-content:space-between;align-items:flex-start">${pillHTML("{Company Logo}")}<div style="text-align:right"><h1 style="margin:0">${pillHTML("{Company Name}")}</h1><div>${pillHTML("{Company Address}")}</div></div></div>` },
  { key: "client", label: "Client", icon: "person", html: () => `<p><strong>For:</strong> ${pillHTML("{Client Name}")}<br/>${pillHTML("{Client Phone}")} · ${pillHTML("{Client Email}")}<br/>${pillHTML("{Client Address}")}</p>` },
  { key: "device", label: "Device / asset", icon: "devices", html: () => `<p><strong>Device:</strong> ${pillHTML("{Asset Brand}")} ${pillHTML("{Asset Model}")} · SN ${pillHTML("{Asset Serial Number}")}</p>` },
  { key: "custom_fields", label: "Form details", icon: "list_alt", html: () => `<div>${pillHTML("{Custom Fields}")}</div>` },
  { key: "items", label: "Line items", icon: "table", html: () => `<h3>Work</h3>${itemsTableHTML()}` },
  { key: "totals", label: "Totals", icon: "functions", html: () => `<p style="text-align:right">Subtotal ${pillHTML("{Subtotal}")}<br/>Tax ${pillHTML("{Tax Value}")}<br/><strong>Total ${pillHTML("{Order Total}")}</strong><br/>Balance due ${pillHTML("{Order Balance Due}")}</p>` },
  { key: "summary", label: "Work summary", icon: "notes", html: () => `<p style="font-size:12px;color:#555">${pillHTML("{Work Summary}")}</p>` },
  { key: "signature", label: "Signature", icon: "draw", html: () => `<div style="margin-top:24px"><div style="border-bottom:1px solid #333;width:260px;height:28px"></div><div style="font-size:11px;color:#555;margin-top:3px">Signature / Date</div></div>` },
  { key: "terms", label: "Terms & notes", icon: "gavel", html: () => `<hr/><p style="font-size:11px;color:#666">Terms: all repairs carry a 90-day warranty on parts and labour unless stated otherwise.</p>` },
  { key: "custom", label: "Blank block", icon: "crop_square", html: () => `<p>New section — type here.</p>` },
];

export default function TemplateEditorDrawer({ initial, onClose, reload }: {
  initial: any; onClose: () => void; reload?: () => void;
}) {
  const edRef = useRef<HTMLDivElement | null>(null);
  const savedRange = useRef<Range | null>(null);
  const [meta, setMeta] = useState({
    name: initial?.name || "", doc_type: initial?.doc_type || "estimate",
    page_size: initial?.page_size || "a4", font_family: initial?.font_family || "Inter",
    is_default: !!initial?.is_default,
    page_w_mm: initial?.page_w_mm ?? null as number | null,
    page_h_mm: initial?.page_h_mm ?? null as number | null,
    margin_mm: initial?.margin_mm ?? null as number | null,
  });
  const [templates, setTemplates] = useState<any[]>([]);
  const [currentId, setCurrentId] = useState<string | null>(initial?.id || null);
  const [actionsOpen, setActionsOpen] = useState(false);
  const [flash, setFlash] = useState<string | null>(null);
  const say = (m: string) => { setFlash(m); setTimeout(() => setFlash(null), 1800); };
  const [mode, setMode] = useState<"edit" | "preview">("edit");
  const [code, setCode] = useState<string | null>(null);   // raw-HTML source view (null = off)
  const [rail, setRail] = useState<"blocks" | "vars" | null>("vars");   // docked variables by default, like a clean editor
  const [showSmart, setShowSmart] = useState(false);                    // Smart builder tucked away by default
  const [settings, setSettings] = useState(false);
  const [addOpen, setAddOpen] = useState(false);
  const [blocks, setBlocks] = useState<{ id: string; label: string }[]>([]);
  const [groups, setGroups] = useState<Grp[]>([]);
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [preview, setPreview] = useState("");
  const [sample, setSample] = useState<string | null>(null);
  const [size, setSize] = useState(14);
  const [aiPrompt, setAiPrompt] = useState("");
  const [aiBusy, setAiBusy] = useState(false);
  const [presets, setPresets] = useState<{ key: string; label: string; doc_type: string }[]>([]);
  const [empty, setEmpty] = useState(false);          // blank page → offer a starting layout
  const [blankOk, setBlankOk] = useState(false);      // "Start blank" dismisses that offer
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => { api.get<any>("/documents/smart-presets").then((d) => setPresets(d?.items || [])).catch(() => setPresets([])); }, []);

  async function buildDoc(preset?: string) {
    if (!edRef.current) return;
    if (!preset && !aiPrompt.trim()) return;
    if ((edRef.current.textContent || "").trim() && !(await confirmDialog("Replace the current document?"))) return;
    setAiBusy(true); setErr(null);
    try {
      const r = await api.post<any>("/documents/ai-generate", { prompt: aiPrompt, doc_type: meta.doc_type, preset: preset || "" });
      if (r?.body_html && edRef.current) { edRef.current.innerHTML = hydrate(r.body_html); refreshBlocks(); focusEditor(); }
    } catch (e: any) { setErr(e?.message || "Could not build the document"); }
    finally { setAiBusy(false); }
  }

  useEffect(() => { api.get<any>("/documents/templates").then((d) => setGroups(d?.variable_groups || [])).catch(() => setGroups([])); }, []);
  useEffect(() => {
    if (!edRef.current) return;
    const seeded = hydrate(initial?.body_html || "");
    edRef.current.innerHTML = seeded && !/^\s*<section/i.test(seeded)
      ? `<section data-block="body" data-label="Body">${seeded}</section>` : (seeded || "");
    refreshBlocks();
    /* eslint-disable-next-line */
  }, []);

  // ── selection helpers (so font/size/colour apply to the last selection) ──
  function saveSel() {
    const s = window.getSelection();
    if (s && s.rangeCount && edRef.current?.contains(s.anchorNode)) savedRange.current = s.getRangeAt(0).cloneRange();
  }
  function restoreSel() {
    const s = window.getSelection();
    if (savedRange.current && s) { s.removeAllRanges(); s.addRange(savedRange.current); }
  }
  function focusEditor() { edRef.current?.focus(); }
  function exec(cmd: string, val?: string) { restoreSel(); document.execCommand(cmd, false, val); focusEditor(); }
  function applyFont(f: string) { restoreSel(); document.execCommand("styleWithCSS", false, "true"); document.execCommand("fontName", false, f); focusEditor(); }
  function applyColor(c: string) { restoreSel(); document.execCommand("styleWithCSS", false, "true"); document.execCommand("foreColor", false, c); focusEditor(); }
  function applyHilite(c: string) { restoreSel(); document.execCommand("styleWithCSS", false, "true"); (document.execCommand("hiliteColor", false, c) || document.execCommand("backColor", false, c)); focusEditor(); }
  function applySize(px: number) {
    restoreSel(); document.execCommand("styleWithCSS", false, "false"); document.execCommand("fontSize", false, "7");
    edRef.current?.querySelectorAll('font[size="7"]').forEach((f) => {
      const s = document.createElement("span"); s.style.fontSize = px + "px";
      while (f.firstChild) s.appendChild(f.firstChild); f.replaceWith(s);
    });
    focusEditor();
  }

  // ── preview ──
  async function fetchPreview() {
    if (!edRef.current) return;
    try { const r = await api.post<any>("/documents/preview", { body_html: serialize(edRef.current), doc_type: meta.doc_type }); setPreview(r?.html || ""); setSample(r?.sample || null); }
    catch { setPreview("<p style='color:#999'>Preview unavailable.</p>"); }
  }
  function toPreview() { if (code !== null) toggleCode(); fetchPreview(); setMode("preview"); }
  function toEdit() { setMode("edit"); }
  function toggleCode() {
    if (code === null) { if (edRef.current) setCode(serialize(edRef.current)); }
    else { if (edRef.current) { edRef.current.innerHTML = hydrate(code); refreshBlocks(); } setCode(null); }
  }

  // ── insert helpers ──
  function insertNode(node: Node) {
    const ed = edRef.current; if (!ed) return;
    const sel = window.getSelection(); let range: Range;
    if (sel && sel.rangeCount && ed.contains(sel.anchorNode)) { range = sel.getRangeAt(0); range.deleteContents(); }
    else { range = document.createRange(); range.selectNodeContents(ed); range.collapse(false); }
    range.insertNode(node);
    const sp = document.createTextNode(" "); range.setStartAfter(node); range.insertNode(sp); range.setStartAfter(sp); range.collapse(true);
    sel?.removeAllRanges(); sel?.addRange(range);
  }
  function insertVar(tok: string) { if (mode !== "edit") setMode("edit"); const t = document.createElement("div"); t.innerHTML = pillHTML(tok); insertNode(t.firstChild as Node); focusEditor(); }
  function insertHTML(html: string) { const t = document.createElement("div"); t.innerHTML = html; const frag = document.createDocumentFragment(); while (t.firstChild) frag.appendChild(t.firstChild); insertNode(frag); focusEditor(); }

  // ── blocks ──
  function refreshBlocks() {
    const ed = edRef.current; if (!ed) return;
    setEmpty(!(ed.textContent || "").trim() && !ed.querySelector("img,table,hr,[data-var]"));
    const secs = Array.from(ed.querySelectorAll(":scope > section")) as HTMLElement[];
    secs.forEach((s) => { if (!s.dataset.bid) s.dataset.bid = Math.random().toString(36).slice(2, 9); });
    setBlocks(secs.map((s) => ({ id: s.dataset.bid!, label: s.dataset.label || "Section" })));
  }
  function addBlock(key: string) {
    const def = BLOCKS.find((b) => b.key === key); if (!def || !edRef.current) return;
    const sec = document.createElement("section");
    sec.dataset.block = key; sec.dataset.label = def.label; sec.dataset.bid = Math.random().toString(36).slice(2, 9);
    sec.innerHTML = def.html();
    edRef.current.appendChild(sec); setAddOpen(false); refreshBlocks();
    sec.scrollIntoView({ behavior: "smooth", block: "center" });
  }
  function jumpBlock(id: string) { edRef.current?.querySelector<HTMLElement>(`section[data-bid="${id}"]`)?.scrollIntoView({ behavior: "smooth", block: "center" }); }
  function removeBlock(id: string) { edRef.current?.querySelector(`section[data-bid="${id}"]`)?.remove(); refreshBlocks(); }
  function moveBlock(dragId: string, overId: string) {
    const ed = edRef.current; if (!ed || dragId === overId) return;
    const a = ed.querySelector<HTMLElement>(`section[data-bid="${dragId}"]`);
    const b = ed.querySelector<HTMLElement>(`section[data-bid="${overId}"]`);
    if (!a || !b) return;
    const rectAfter = b.compareDocumentPosition(a) & Node.DOCUMENT_POSITION_FOLLOWING;
    b.parentNode!.insertBefore(a, rectAfter ? b : b.nextSibling); refreshBlocks();
  }

  // ── template list + in-place switching (Orderry-style flow) ──
  async function loadList() {
    try { const d = await api.get<any>("/documents/templates"); setTemplates(d?.items || []); } catch { /* keep prior */ }
  }
  useEffect(() => { loadList(); /* eslint-disable-next-line */ }, []);
  function openTpl(t: any) {
    setActionsOpen(false); setCode(null); setMode("edit");
    setMeta({ name: t.name || "", doc_type: t.doc_type || "estimate", page_size: t.page_size || "a4", font_family: t.font_family || "Inter", is_default: !!t.is_default, page_w_mm: t.page_w_mm ?? null, page_h_mm: t.page_h_mm ?? null, margin_mm: t.margin_mm ?? null });
    setCurrentId(t.id || null);
    requestAnimationFrame(() => { if (edRef.current) { edRef.current.innerHTML = hydrate(t.body_html || ""); refreshBlocks(); } });
  }
  function newDoc() {
    setActionsOpen(false); setCode(null); setMode("edit");
    setMeta({ name: "", doc_type: "estimate", page_size: "a4", font_family: "Inter", is_default: false, page_w_mm: null, page_h_mm: null, margin_mm: null });
    setCurrentId(null);
    requestAnimationFrame(() => { if (edRef.current) { edRef.current.innerHTML = ""; refreshBlocks(); } });
  }
  function bodyNow(): string { if (code !== null && edRef.current) { edRef.current.innerHTML = hydrate(code); setCode(null); } return edRef.current ? serialize(edRef.current) : ""; }

  async function save() {
    if (!meta.name.trim()) { setErr("Template name is required"); return; }
    if (!edRef.current) return;
    setBusy(true); setErr(null);
    const payload = { ...meta, body_html: bodyNow() };
    try {
      if (currentId) await api.put(`/documents/templates/${currentId}`, payload);
      else { const r = await api.post<any>("/documents/templates", payload); setCurrentId(r?.id || null); }
      say("Saved"); await loadList(); reload?.();
    } catch (e: any) { setErr(e?.message || "Could not save"); }
    finally { setBusy(false); }
  }
  async function duplicate() {
    if (!edRef.current) return; setActionsOpen(false); setErr(null);
    try {
      const r = await api.post<any>("/documents/templates", { ...meta, name: (meta.name || "Template") + " (copy)", is_default: false, body_html: bodyNow() });
      say("Duplicated"); await loadList(); reload?.();
      if (r?.id) { setCurrentId(r.id); setMeta((m) => ({ ...m, name: (m.name || "Template") + " (copy)", is_default: false })); }
    } catch (e: any) { setErr(e?.message || "Could not duplicate"); }
  }
  async function setDefault() {
    setActionsOpen(false); if (!currentId) { setErr("Save the template first"); return; }
    try { await api.put(`/documents/templates/${currentId}`, { ...meta, is_default: true, body_html: bodyNow() }); setMeta((m) => ({ ...m, is_default: true })); say("Set as default"); await loadList(); reload?.(); }
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

  const ql = q.trim().toLowerCase();
  // "Most used" sits on top, open, so the everyday fields are one click away.
  const shown = useMemo(() => {
    const all = new Set(groups.flatMap((g) => g.items));
    const most = { group: "Most used", items: MOST_USED.filter((t) => all.has(t)) };
    const list = (most.items.length && !ql ? [most, ...groups] : groups);
    return list.map((g) => ({ ...g, items: ql ? g.items.filter((i) => i.toLowerCase().includes(ql)) : g.items })).filter((g) => g.items.length);
  }, [groups, ql]);
  // paper-true canvas: real physical size → px at 96dpi (1mm = 3.7795px)
  const MM = 3.7795;
  const PRESET_MM: Record<string, [number, number | null]> = { a4: [210, 297], letter: [215.9, 279.4], receipt80: [80, null], receipt58: [58, null] };
  function pageMM(): { w: number; h: number | null } {
    if (meta.page_size === "label" && meta.page_w_mm && meta.page_h_mm) return { w: +meta.page_w_mm, h: +meta.page_h_mm };
    if (PRESET_MM[meta.page_size]) { const [w, h] = PRESET_MM[meta.page_size]; return { w, h }; }
    return { w: 210, h: 297 };
  }
  const _pm = pageMM();
  const pageW = Math.round(_pm.w * MM);
  const pageMinH = _pm.h ? Math.round(_pm.h * MM) : 420;
  const padPx = Math.round(((meta.margin_mm ?? (meta.page_size === "a4" || meta.page_size === "letter" ? 10 : 4)) as number) * MM);
  const dragId = useRef<string | null>(null);

  async function printDoc() {
    setActionsOpen(false);
    try {
      const r = await api.post<any>("/documents/print-view", {
        body_html: bodyNow(), doc_type: meta.doc_type, page_size: meta.page_size,
        page_w_mm: meta.page_w_mm || null, page_h_mm: meta.page_h_mm || null,
        margin_mm: meta.margin_mm ?? null, font_family: meta.font_family || "Inter",
      });
      const html = r?.html; if (!html) return;
      printHtmlInFrame(html);
    } catch (e: any) { setErr(e?.message || "Print failed"); }
  }

  const TB = ({ cmd, val, icon, title }: { cmd: string; val?: string; icon: string; title: string }) => (
    <button type="button" aria-label={title} title={title} onMouseDown={(e) => { e.preventDefault(); exec(cmd, val); }} className="w-8 h-8 rounded-md hover:bg-slate-200/70 flex items-center justify-center text-ink-secondary"><M n={icon} c="text-[18px]" /></button>
  );

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-slate-100">
      {/* top bar */}
      <div className="flex items-center gap-3 px-4 h-14 bg-white border-b border-slate-200 shrink-0">
        <button aria-label="Close" onClick={onClose} title="Close" className="w-9 h-9 rounded-lg hover:bg-slate-200/60 flex items-center justify-center text-ink-tertiary"><M n="arrow_back" c="text-lg" /></button>
        <button onClick={newDoc} className="h-9 px-3 rounded-lg border border-slate-200 text-ink-secondary font-semibold text-xs hover:bg-slate-100 hover:border-slate-300 flex items-center gap-1 shrink-0"><M n="add" c="text-[16px]" /> New</button>
        {templates.length > 0 && (
          <select value={currentId || ""} onChange={(e) => { const t = templates.find((x) => x.id === e.target.value); if (t) openTpl(t); }}
            className="h-9 max-w-[180px] bg-white border border-slate-200 rounded-lg text-sm px-2 focus:outline-none focus:ring-1 focus:ring-primary">
            <option value="" disabled>Switch template…</option>
            {templates.map((t) => <option key={t.id} value={t.id}>{t.name}{t.is_default ? " (default)" : ""}</option>)}
          </select>
        )}
        <input value={meta.name} onChange={(e) => setMeta({ ...meta, name: e.target.value })} placeholder="Untitled template" className="text-[15px] font-semibold text-ink-primary bg-transparent border-0 focus:outline-none focus:bg-slate-100 rounded px-2 py-1 w-44" />
        {/* Actions menu — replaces separate rename/delete icons */}
        <div className="relative">
          <button aria-label="Actions" onClick={() => setActionsOpen((o) => !o)} title="Actions" className="w-9 h-9 rounded-lg hover:bg-slate-200/60 flex items-center justify-center text-ink-tertiary"><M n="more_vert" c="text-lg" /></button>
          {actionsOpen && (
            <div className="absolute left-0 top-11 z-20 w-48 bg-white border border-slate-200 rounded-xl shadow-drawer p-1">
              <button onClick={duplicate} className="w-full flex items-center gap-2 px-2 py-1.5 rounded-md hover:bg-slate-100 text-left text-sm text-ink-secondary"><M n="content_copy" c="text-[16px] text-ink-tertiary" /> Duplicate</button>
              <button onClick={setDefault} className="w-full flex items-center gap-2 px-2 py-1.5 rounded-md hover:bg-slate-100 text-left text-sm text-ink-secondary"><M n="check_circle" c="text-[16px] text-ink-tertiary" /> Set as default</button>
              <button onClick={del} className="w-full flex items-center gap-2 px-2 py-1.5 rounded-md hover:bg-rose-50 text-left text-sm text-rose-600"><M n="delete" c="text-[16px]" /> Delete</button>
            </div>
          )}
        </div>
        <div className="mx-auto inline-flex rounded-full border border-slate-300 overflow-hidden text-xs font-semibold">
          <button onClick={toEdit} className={`px-4 py-1.5 flex items-center gap-1 ${mode === "edit" ? "bg-primary text-white" : "text-ink-secondary hover:bg-slate-100"}`}><M n="edit" c="text-[15px]" /> Edit</button>
          <button onClick={toPreview} className={`px-4 py-1.5 flex items-center gap-1 ${mode === "preview" ? "bg-primary text-white" : "text-ink-secondary hover:bg-slate-100"}`}><M n="visibility" c="text-[15px]" /> Preview</button>
        </div>
        <div className="relative">
          <button aria-label="Document settings" onClick={() => setSettings((s) => !s)} title="Document settings" className="w-9 h-9 rounded-lg hover:bg-slate-200/60 flex items-center justify-center text-ink-tertiary"><M n="tune" c="text-lg" /></button>
          {settings && (
            <div className="absolute right-0 top-11 z-20 w-64 bg-white border border-slate-200 rounded-xl shadow-drawer p-3 space-y-3">
              <div><label className="block text-[10px] font-semibold text-ink-tertiary uppercase mb-1">Type</label>
                <select value={meta.doc_type} onChange={(e) => setMeta({ ...meta, doc_type: e.target.value })} className="w-full bg-white border border-slate-200 rounded-lg text-sm px-2 py-1.5 capitalize">{DOC_TYPES.map((t) => <option key={t} value={t}>{t.replace(/_/g, " ")}</option>)}</select></div>
              <div><label className="block text-[10px] font-semibold text-ink-tertiary uppercase mb-1">Paper / label size</label>
                <select value={meta.page_size} onChange={(e) => setMeta({ ...meta, page_size: e.target.value })} className="w-full bg-white border border-slate-200 rounded-lg text-sm px-2 py-1.5">
                  <option value="a4">A4 (210×297mm)</option>
                  <option value="letter">Letter (8.5×11in)</option>
                  <option value="receipt80">Receipt 80mm</option>
                  <option value="receipt58">Receipt 58mm</option>
                  <option value="label">Custom label (mm)…</option>
                </select></div>
              {meta.page_size === "label" && (
                <div className="grid grid-cols-2 gap-2">
                  <div><label className="block text-[10px] font-semibold text-ink-tertiary uppercase mb-1">Width mm</label>
                    <input type="number" value={meta.page_w_mm ?? ""} onChange={(e) => setMeta({ ...meta, page_w_mm: e.target.value ? +e.target.value : null })} placeholder="40" className="w-full bg-white border border-slate-200 rounded-lg text-sm px-2 py-1.5" /></div>
                  <div><label className="block text-[10px] font-semibold text-ink-tertiary uppercase mb-1">Height mm</label>
                    <input type="number" value={meta.page_h_mm ?? ""} onChange={(e) => setMeta({ ...meta, page_h_mm: e.target.value ? +e.target.value : null })} placeholder="30" className="w-full bg-white border border-slate-200 rounded-lg text-sm px-2 py-1.5" /></div>
                </div>
              )}
              <div><label className="block text-[10px] font-semibold text-ink-tertiary uppercase mb-1">Margin mm (blank = auto)</label>
                <input type="number" value={meta.margin_mm ?? ""} onChange={(e) => setMeta({ ...meta, margin_mm: e.target.value ? +e.target.value : null })} placeholder={(meta.page_size === "a4" || meta.page_size === "letter") ? "10" : "0"} className="w-full bg-white border border-slate-200 rounded-lg text-sm px-2 py-1.5" /></div>
              <div><label className="block text-[10px] font-semibold text-ink-tertiary uppercase mb-1">Default font</label>
                <select value={meta.font_family} onChange={(e) => setMeta({ ...meta, font_family: e.target.value })} className="w-full bg-white border border-slate-200 rounded-lg text-sm px-2 py-1.5">{FONTS.map((f) => <option key={f} value={f}>{f}</option>)}</select></div>
              <label className="flex items-center gap-2 text-sm text-ink-secondary"><input type="checkbox" checked={meta.is_default} onChange={(e) => setMeta({ ...meta, is_default: e.target.checked })} /> Default for this type</label>
            </div>
          )}
        </div>
        <button onClick={() => setRail((r) => r === "blocks" ? null : "blocks")} className={`h-9 px-3 rounded-lg border text-xs font-semibold flex items-center gap-1 ${rail === "blocks" ? "border-primary text-primary bg-primary/5" : "border-slate-200 text-ink-secondary hover:bg-slate-100"}`}><M n="dashboard" c="text-[16px]" /> Blocks</button>
        <button onClick={() => setRail((r) => r === "vars" ? null : "vars")} className={`h-9 px-3 rounded-lg border text-xs font-semibold flex items-center gap-1 ${rail === "vars" ? "border-primary text-primary bg-primary/5" : "border-slate-200 text-ink-secondary hover:bg-slate-100"}`}><M n="data_object" c="text-[16px]" /> Variables</button>
        {flash && <span className="text-xs text-emerald-600 font-semibold">{flash}</span>}
        <button aria-label="Print at exact size" onClick={printDoc} title="Print at exact size" className="w-9 h-9 rounded-lg border border-slate-200 hover:bg-slate-100 flex items-center justify-center text-ink-secondary"><M n="print" c="text-lg" /></button>
        <button onClick={save} disabled={busy} className="h-9 px-4 rounded-lg bg-primary text-white font-semibold text-xs hover:bg-primary-container disabled:opacity-60">{busy ? "Saving…" : "Save"}</button>
      </div>

      {/* formatting toolbar (edit only) */}
      {mode === "edit" && (
        <div className="px-4 py-1.5 bg-white border-b border-slate-200 flex items-center gap-1 flex-wrap shrink-0">
          <select title="Font" onMouseDown={saveSel} onChange={(e) => applyFont(e.target.value)} defaultValue="" className="h-8 border border-slate-200 rounded-md text-xs px-1.5 text-ink-secondary bg-white">
            <option value="" disabled>Font</option>{FONTS.map((f) => <option key={f} value={f} style={{ fontFamily: f }}>{f}</option>)}
          </select>
          <select title="Size" value={size} onMouseDown={saveSel} onChange={(e) => { const px = Number(e.target.value); setSize(px); applySize(px); }} className="h-8 border border-slate-200 rounded-md text-xs px-1.5 text-ink-secondary bg-white w-16">
            {SIZES.map((s) => <option key={s} value={s}>{s}px</option>)}
          </select>
          <label title="Text colour" className="w-8 h-8 rounded-md hover:bg-slate-200/70 flex items-center justify-center text-ink-secondary cursor-pointer relative"><M n="format_color_text" c="text-[18px]" /><input type="color" onMouseDown={saveSel} onChange={(e) => applyColor(e.target.value)} className="absolute inset-0 opacity-0 cursor-pointer" /></label>
          <label title="Highlight" className="w-8 h-8 rounded-md hover:bg-slate-200/70 flex items-center justify-center text-ink-secondary cursor-pointer relative"><M n="format_ink_highlighter" c="text-[18px]" /><input type="color" onMouseDown={saveSel} onChange={(e) => applyHilite(e.target.value)} className="absolute inset-0 opacity-0 cursor-pointer" /></label>
          <span className="w-px h-5 bg-slate-200 mx-1" />
          <TB cmd="bold" icon="format_bold" title="Bold" />
          <TB cmd="italic" icon="format_italic" title="Italic" />
          <TB cmd="underline" icon="format_underlined" title="Underline" />
          <TB cmd="strikeThrough" icon="strikethrough_s" title="Strikethrough" />
          <TB cmd="formatBlock" val="h1" icon="title" title="Heading 1" />
          <TB cmd="formatBlock" val="h2" icon="text_fields" title="Heading 2" />
          <TB cmd="insertUnorderedList" icon="format_list_bulleted" title="Bullet list" />
          <TB cmd="justifyLeft" icon="format_align_left" title="Align left" />
          <TB cmd="justifyCenter" icon="format_align_center" title="Align center" />
          <TB cmd="justifyRight" icon="format_align_right" title="Align right" />
          <TB cmd="insertHorizontalRule" icon="horizontal_rule" title="Divider" />
          <span className="w-px h-5 bg-slate-200 mx-1" />
          <button type="button" onMouseDown={(e) => { e.preventDefault(); insertVar("{Company Logo}"); }} className="h-8 px-2 rounded-md hover:bg-slate-200/70 flex items-center gap-1 text-xs text-ink-secondary"><M n="image" c="text-[16px]" /> Logo</button>
          <button type="button" onMouseDown={(e) => { e.preventDefault(); insertHTML(itemsTableHTML()); }} className="h-8 px-2 rounded-md hover:bg-slate-200/70 flex items-center gap-1 text-xs text-ink-secondary"><M n="table" c="text-[16px]" /> Items table</button>
          <button aria-label="Edit HTML source" type="button" title="Edit HTML source" onClick={toggleCode} className={`w-8 h-8 rounded-md flex items-center justify-center ${code !== null ? "bg-primary/10 text-primary" : "hover:bg-slate-200/70 text-ink-secondary"}`}><M n="code" c="text-[18px]" /></button>
          <button type="button" onClick={() => setShowSmart((s) => !s)} className={`h-8 px-2 rounded-md flex items-center gap-1 text-xs ml-auto ${showSmart ? "bg-primary/10 text-primary" : "hover:bg-slate-200/70 text-ink-secondary"}`}><M n="bolt" c="text-[16px]" /> Smart builder</button>
        </div>
      )}

      {/* Smart builder — RepairOS's own document intelligence (offline presets + describe) */}
      {mode === "edit" && showSmart && (
        <div className="px-4 py-2 bg-primary/5 border-b border-slate-200 flex items-center gap-2 flex-wrap shrink-0">
          <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-primary"><M n="bolt" c="text-[15px]" /> Smart builder</span>
          {presets.map((p) => (
            <button key={p.key} onClick={() => buildDoc(p.key)} disabled={aiBusy} className="text-[11px] px-2 py-1 rounded-full border border-slate-200 bg-white hover:border-primary hover:text-primary disabled:opacity-60">{p.label}</button>
          ))}
          <span className="w-px h-5 bg-slate-200 mx-0.5" />
          <input value={aiPrompt} onChange={(e) => setAiPrompt(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") buildDoc(); }}
            placeholder="…or describe one — e.g. “estimate with logo, parts table, deposit line and signature”"
            className="flex-1 min-w-[220px] bg-white border border-slate-200 rounded-lg text-xs px-3 py-2 focus:outline-none focus:ring-1 focus:ring-primary" />
          <button onClick={() => buildDoc()} disabled={aiBusy || !aiPrompt.trim()} className="h-8 px-3 rounded-lg bg-primary text-white text-xs font-semibold disabled:opacity-60 flex items-center gap-1 whitespace-nowrap">{aiBusy ? "Building…" : "Build"}</button>
        </div>
      )}

      {/* body: blocks rail | desk | vars rail */}
      <div className="flex-1 min-h-0 flex">
        {/* Blocks rail (edit only) */}
        {mode === "edit" && rail === "blocks" && (
          <aside className="w-56 bg-white border-r border-slate-200 flex flex-col shrink-0">
            <div className="px-3 py-2.5 border-b border-slate-200 flex items-center justify-between">
              <span className="text-[11px] font-bold text-ink-secondary uppercase tracking-wide">Blocks</span>
              <div className="relative">
                <button onClick={() => setAddOpen((a) => !a)} className="text-xs font-semibold text-primary flex items-center gap-0.5 hover:underline"><M n="add" c="text-[15px]" /> Add</button>
                {addOpen && (
                  <div className="absolute right-0 top-7 z-20 w-48 bg-white border border-slate-200 rounded-xl shadow-drawer p-1">
                    {BLOCKS.map((b) => (
                      <button key={b.key} onClick={() => addBlock(b.key)} className="w-full flex items-center gap-2 px-2 py-1.5 rounded-md hover:bg-slate-100 text-left text-sm text-ink-secondary"><M n={b.icon} c="text-[16px] text-ink-tertiary" /> {b.label}</button>
                    ))}
                  </div>
                )}
              </div>
            </div>
            <div className="flex-1 overflow-y-auto p-2 space-y-1">
              {blocks.length === 0 && <p className="text-[11px] text-ink-tertiary px-1 py-2">Add blocks to build your document, or just type on the page.</p>}
              {blocks.map((b) => (
                <div key={b.id} draggable onDragStart={() => { dragId.current = b.id; }} onDragOver={(e) => e.preventDefault()} onDrop={() => { if (dragId.current) moveBlock(dragId.current, b.id); dragId.current = null; }}
                  className="group flex items-center gap-1.5 px-2 py-1.5 rounded-lg border border-slate-200 bg-white hover:border-slate-300 cursor-grab active:cursor-grabbing">
                  <M n="drag_indicator" c="text-[16px] text-ink-tertiary" />
                  <button onClick={() => jumpBlock(b.id)} className="flex-1 text-left text-xs font-medium text-ink-primary truncate">{b.label}</button>
                  <button aria-label="Remove" onClick={() => removeBlock(b.id)} title="Remove" className="w-6 h-6 rounded grid place-items-center text-ink-tertiary opacity-0 group-hover:opacity-100 hover:bg-rose-50 hover:text-rose-500"><M n="close" c="text-[13px]" /></button>
                </div>
              ))}
            </div>
          </aside>
        )}

        {/* desk */}
        <div className="relative flex-1 min-w-0 overflow-auto">
          {code !== null && mode === "edit" && (
            <div className="absolute inset-0 z-10 flex flex-col bg-slate-900">
              <div className="px-4 py-1.5 text-[11px] font-semibold text-slate-300 uppercase tracking-wide border-b border-slate-700 flex items-center gap-1"><M n="code" c="text-[14px]" /> HTML source — click the &lt;/&gt; button again to apply</div>
              <textarea value={code} onChange={(e) => setCode(e.target.value)} spellCheck={false}
                className="flex-1 w-full bg-slate-900 text-slate-100 font-mono text-xs p-4 outline-none resize-none" />
            </div>
          )}
          <div className="relative min-h-full flex justify-center py-8 px-6">
            {mode === "edit" && empty && !blankOk && code === null && presets.length > 0 && (
              <div className="absolute top-20 left-1/2 -translate-x-1/2 z-[5] w-[420px] max-w-[90%] bg-white rounded-2xl border border-slate-200 shadow-clay-drawer p-5">
                <div className="text-sm font-semibold text-ink-primary">Start from a layout</div>
                <div className="text-[11px] text-ink-tertiary mb-3">Pick one and change anything you like. Your logo and details fill in automatically.</div>
                <div className="space-y-1.5">
                  {[...presets].sort((a, b) => Number(b.doc_type === meta.doc_type) - Number(a.doc_type === meta.doc_type)).map((p) => (
                    <button key={p.key} onClick={() => { setBlankOk(true); buildDoc(p.key); }} disabled={aiBusy}
                      className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-xl border text-left transition-colors disabled:opacity-60 ${p.doc_type === meta.doc_type ? "border-primary/40 bg-primary/5" : "border-slate-200 hover:border-slate-300"}`}>
                      <M n="description" c="text-base text-primary" />
                      <span className="text-sm text-ink-primary flex-1">{p.label}</span>
                      {p.doc_type === meta.doc_type && <span className="text-[10px] font-semibold text-primary">Suggested</span>}
                    </button>
                  ))}
                </div>
                <button onClick={() => { setBlankOk(true); focusEditor(); }} className="mt-3 w-full text-center text-xs font-semibold text-ink-secondary hover:text-ink-primary py-1.5">Start with a blank page</button>
              </div>
            )}
            <div ref={edRef} contentEditable={mode === "edit"} suppressContentEditableWarning onInput={refreshBlocks} onKeyUp={saveSel} onMouseUp={saveSel}
              className={`doc-canvas bg-white shadow-lg outline-none text-sm text-ink-primary ${mode === "edit" ? "editing" : ""}`}
              style={{ width: pageW, maxWidth: "100%", minHeight: pageMinH, padding: padPx, fontFamily: meta.font_family || "Inter", lineHeight: 1.5, display: mode === "edit" ? "block" : "none" }} />
            {mode === "preview" && (
              <div className="doc-canvas bg-white shadow-lg text-sm text-ink-primary" style={{ width: pageW, maxWidth: "100%", minHeight: pageMinH, padding: padPx, fontFamily: meta.font_family || "Inter", lineHeight: 1.5 }} dangerouslySetInnerHTML={{ __html: preview ? sanitizeHtml(preview) : "<p style='color:#999'>Loading preview…</p>" }} />
            )}
          </div>
          {mode === "preview" && sample && <div className="absolute top-3 left-1/2 -translate-x-1/2 text-[11px] text-ink-tertiary bg-white/80 border border-slate-200 rounded-full px-3 py-1">Preview using {sample}</div>}
        </div>

        {/* variables rail */}
        {rail === "vars" && (
          <aside className="w-[300px] bg-white border-l border-slate-200 flex flex-col shrink-0">
            <div className="p-2.5 border-b border-slate-200 flex items-center gap-2">
              <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search variables…" className="flex-1 bg-white border border-slate-200 rounded-md text-xs px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-primary" />
              <button aria-label="Close" onClick={() => setRail("blocks")} className="w-7 h-7 rounded-md hover:bg-slate-100 flex items-center justify-center text-ink-tertiary"><M n="close" c="text-base" /></button>
            </div>
            <div className="flex-1 overflow-y-auto p-1.5 space-y-1">
              <p className="text-[11px] text-ink-tertiary px-2 pt-1 pb-2">Click a variable to drop it into the page at your cursor.</p>
              {shown.length === 0 && <div className="text-xs text-ink-tertiary px-2 py-3">No matches.</div>}
              {shown.map((g) => {
                const isOpen = ql ? true : (open[g.group] ?? g.group === "Most used");
                return (
                  <div key={g.group}>
                    <button type="button" onClick={() => setOpen((o) => ({ ...o, [g.group]: !isOpen }))} className="w-full flex items-center gap-2 px-2 py-1.5 rounded-md hover:bg-slate-200/60 text-left"><M n={GROUP_ICON[g.group] || "label"} c="text-[16px] text-ink-tertiary shrink-0" /><span className="text-[11px] font-bold text-ink-secondary uppercase tracking-wide flex-1">{g.group}</span><M n={isOpen ? "expand_less" : "expand_more"} c="text-sm text-ink-tertiary" /></button>
                    {isOpen && <div className="pb-1">{g.items.map((tok) => (<button key={tok} type="button" title={`Insert ${tok}`} onMouseDown={(e) => { e.preventDefault(); insertVar(tok); }} className="group/var w-full flex items-center gap-1.5 pl-3 pr-2 py-1 text-[13px] text-primary text-left rounded hover:bg-primary/5"><span className="truncate">{tok.replace(/^\{|\}$/g, "")}</span><M n="add" c="text-[13px] text-ink-tertiary opacity-0 group-hover/var:opacity-100 ml-auto shrink-0" /></button>))}</div>}
                  </div>
                );
              })}
            </div>
          </aside>
        )}
      </div>

      {err && <div className="px-4 py-2 bg-rose-50 border-t border-rose-200 text-rose-700 text-xs shrink-0">{err}</div>}
    </div>
  );
}
