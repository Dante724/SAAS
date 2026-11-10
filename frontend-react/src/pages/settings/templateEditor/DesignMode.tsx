// "Design" view: drag-and-drop page builder (GrapesJS core, BSD-3) on a page-sized sheet.
// Tekly draws the blocks, fields and simple style panels itself; GrapesJS's full style panel is
// only shown under "More", restyled in templateEditor.css. No library branding or toolbar shows.
import { useEffect, useRef, useState, type ReactNode } from "react";
import grapesjs, { type BlockProperties, type Component, type Editor } from "grapesjs";
import "grapesjs/dist/css/grapes.min.css";
import {
  AlignCenter, AlignLeft, AlignRight, Bold, ChevronDown, ChevronRight, Columns2, Heading, ImageIcon, LayoutTemplate, Minus, Redo2,
  Signature, Sigma, SquareDashedBottom, Store, Table2, Trash2, Type, Undo2,
} from "lucide-react";
import { addTeklyTypes, baseConfig, designChip, exportDesign, setDesignDisplay, snapshotStyles, toDesignView } from "./grapesSetup";
import { styleText } from "./templateHtml";
import FieldsPanel, { type VarGroup } from "./FieldsPanel";
import { ALIGN, COLOURS, FONTS, SIZES, SPACING, applySimple, readSimple, type Simple, type Style } from "./simpleStyle";
import type { FieldDisplay } from "./fieldValues";
import type { PageBox } from "./types";
import "./templateEditor.css";

const td = (s: string) => `<td style="border:1px solid #ddd;padding:6px">${s}</td>`;
const th = (s: string) => `<th style="border:1px solid #ddd;padding:6px;text-align:left;background:#f5f5f5">${s}</th>`;
const chip = (t: string) => designChip(t);

type Def = { id: string; label: string; icon: ReactNode; content: BlockProperties["content"]; activate?: boolean };
const LAYOUT: Def[] = [
  { id: "text", label: "Text", icon: <Type size={18} />, content: "<p>Write something here.</p>" },
  { id: "heading", label: "Heading", icon: <Heading size={18} />, content: "<h2>Heading</h2>" },
  { id: "image", label: "Image", icon: <ImageIcon size={18} />, content: { type: "image", style: { "max-width": "100%" } }, activate: true },
  { id: "logo", label: "Shop logo", icon: <Store size={18} />, content: designChip("{Company Logo}", true) },
  { id: "divider", label: "Divider", icon: <Minus size={18} />, content: '<hr style="border:0;border-top:1px solid #ddd;margin:12px 0">' },
  { id: "columns", label: "Two columns", icon: <Columns2 size={18} />, content: '<div style="display:flex;gap:16px"><div style="flex:1"><p>Left column</p></div><div style="flex:1"><p>Right column</p></div></div>' },
  { id: "items", label: "Items table", icon: <Table2 size={18} />, content: `<table style="width:100%;border-collapse:collapse;margin:8px 0"><thead><tr>${["#", "Item", "Qty", "Price", "Amount"].map(th).join("")}</tr></thead><tbody><tr>${["{Row Number}", "{Product Name}", "{Product Qty}", "{Product Price}", "{Product Amount}"].map((t) => td(chip(t))).join("")}</tr></tbody></table>` },
  { id: "totals", label: "Totals", icon: <Sigma size={18} />, content: `<p style="text-align:right">Subtotal ${chip("{Subtotal}")}<br>Tax ${chip("{Tax Value}")}<br><strong>Total ${chip("{Order Total}")}</strong><br>Balance due ${chip("{Order Balance Due}")}</p>` },
  { id: "signature", label: "Signature line", icon: <Signature size={18} />, content: '<div style="margin-top:24px"><div style="border-bottom:1px solid #333;width:260px;height:28px"></div><div style="font-size:11px;color:#555;margin-top:3px">Signature / Date</div></div>' },
];

// The full GrapesJS style panel, shown only under "More".
const SECTORS = [
  { name: "Spacing", open: false, properties: ["margin", "padding"] },
  { name: "Size", open: false, properties: ["width", "height", "max-width"] },
  { name: "Text", open: false, properties: ["line-height", "letter-spacing", "text-decoration", "font-style"] },
  { name: "Border", open: false, properties: ["border", "border-radius"] },
  { name: "Background", open: false, properties: ["background-color"] },
];

function frameCss(page: PageBox, templateCss: string) {
  return `*{box-sizing:border-box}html,body{background:#fff}body{margin:0;padding:${page.pad}px;min-height:${page.minH}px;font-family:${page.font},Arial,sans-serif;color:#111;font-size:13px;line-height:1.5}
table{border-collapse:collapse}img{max-width:100%}
.tok-pill{display:inline-block;background:#eef2ff;color:#3730a3;border:1px solid #c7d2fe;border-radius:6px;padding:0 6px;margin:0 1px;font-size:.85em;font-weight:600;line-height:1.5;white-space:nowrap;font-family:system-ui,sans-serif}
.tok-pill.tok-logic{background:#fff7ed;color:#9a3412;border-color:#fed7aa}
div.tok-pill{display:block;width:max-content;margin:2px 0}
.tok-real{background:rgba(0,89,186,.06);box-shadow:inset 0 -1px 0 rgba(0,89,186,.45);border-radius:2px}
div.tok-real{display:block}
.tok-sample{color:#64748b;font-style:italic}
${templateCss}`;
}
/** Editor-only marks on the sheet: the printable area (inside the margins) and where page 1 ends. */
function guideCss(page: PageBox) {
  const end = page.h ? `html::after{content:"End of page 1";position:absolute;left:0;right:0;top:${page.h}px;border-top:2px dashed #94a3b8;font:11px/1.6 system-ui,sans-serif;color:#64748b;text-align:right;padding-right:6px;pointer-events:none}` : "";
  return `html{position:relative}body::before{content:"";position:absolute;left:${page.pad}px;right:${page.pad}px;top:${page.pad}px;${page.h ? `height:${page.h - 2 * page.pad}px` : "bottom:" + page.pad + "px"};outline:1px dashed #cbd5e1;pointer-events:none}${end}
.gjs-selected{outline:2px solid #0059ba !important;outline-offset:-2px}.gjs-hovered{outline:1px solid #2c72d9 !important}`;
}

export default function DesignMode({ html, onChange, groups, page, display, bindInsert, bindFlush, onSaveShortcut }: {
  html: string; onChange: (html: string) => void; groups: VarGroup[]; page: PageBox;
  display: (tok: string) => FieldDisplay;
  bindInsert?: (fn: ((tok: string) => void) | null) => void;
  /** Gets a function that finishes any text being typed and returns the latest HTML (null = unchanged). */
  bindFlush?: (fn: (() => Promise<string | null>) | null) => void;
  onSaveShortcut?: () => void;
}) {
  const canvasEl = useRef<HTMLDivElement | null>(null);
  const styleEl = useRef<HTMLDivElement | null>(null);
  const traitEl = useRef<HTMLDivElement | null>(null);
  const edRef = useRef<Editor | null>(null);
  const change = useRef(onChange); change.current = onChange;
  const saveKey = useRef(onSaveShortcut); saveKey.current = onSaveShortcut;
  const [ready, setReady] = useState(false);
  const [sel, setSel] = useState<{ name: string; style: Style; text: boolean; traits: boolean } | null>(null);
  const [outlines, setOutlines] = useState(true);
  const [more, setMore] = useState(false);
  const [layoutOpen, setLayoutOpen] = useState(true);

  useEffect(() => {
    setDesignDisplay(display);
    const load = toDesignView(html);
    const editor = grapesjs.init({
      ...baseConfig(),
      container: canvasEl.current!,
      height: "100%", width: "auto",
      plugins: [addTeklyTypes],
      components: load.body,
      blockManager: { custom: true },
      styleManager: { appendTo: styleEl.current!, sectors: SECTORS },
      traitManager: { appendTo: traitEl.current! },
      layerManager: { appendTo: document.createElement("div") },
      deviceManager: { devices: [{ id: "page", name: "Page", width: `${page.w}px` }] },
      protectedCss: frameCss(page, styleText(load.styles)),
      canvasCss: guideCss(page),
      assetManager: { embedAsBase64: true, upload: false },
      richTextEditor: { actions: ["bold", "italic", "underline", "link"] },
    });
    edRef.current = editor;
    let snap = new Map<string, Record<string, string>>();
    let initial: string | null = null;
    const latest = () => {
      if (initial === null) return null;
      const out = exportDesign(editor, load, snap);
      return out !== initial ? out : null;
    };
    const readSel = () => {
      const c = editor.getSelected();
      if (!c) { setSel(null); return; }
      setSel({ name: c.getName() || String(c.get("type") || "Element"), style: { ...(c.getStyle() as Style) }, text: c.get("type") !== "image", traits: c.getTraits().length > 0 });
    };
    editor.on("load", () => {
      snap = snapshotStyles(editor);
      initial = exportDesign(editor, load, snap);
      editor.runCommand("core:component-outline");
      // Ctrl/Cmd+S inside the page saves too (the frame has its own keyboard events).
      editor.Canvas.getDocument()?.addEventListener("keydown", (e: KeyboardEvent) => {
        if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") { e.preventDefault(); saveKey.current?.(); }
      });
      setReady(true);
    });
    editor.on("update", () => { const out = latest(); if (out !== null) change.current(out); });
    editor.on("component:styleUpdate", readSel);
    bindFlush?.(async () => {
      const s = editor.getSelected() as any;
      if (editor.getEditing() && s?.view?.disableEditing) await s.view.disableEditing();
      return latest();
    });
    editor.on("component:selected component:deselected", () => {
      const c = editor.getSelected();
      // A made-up id and a tooltip title aren't things to edit here; image alt text and link
      // addresses are, so those settings stay.
      if (c) { if (c.getTrait("id")) c.removeTrait("id"); if (c.getTrait("title")) c.removeTrait("title"); }
      readSel();
    });
    return () => { bindFlush?.(null); setDesignDisplay(null); edRef.current = null; editor.destroy(); };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Insert a field: into the text being edited, after the selected element, or at the end.
  useEffect(() => {
    bindInsert?.((tok: string) => bindInsertCall(edRef, tok));
    return () => bindInsert?.(null);
  }, [bindInsert]);

  function addBlock(content: BlockProperties["content"], activate?: boolean) {
    const ed = edRef.current; if (!ed) return;
    const s = ed.getSelected();
    const parent = s?.parent();
    const added = (s && parent ? parent.append(content as any, { at: s.index() + 1 }) : ed.getWrapper()!.append(content as any)) as Component[];
    if (added?.[0]) { ed.select(added[0]); if (activate) ed.runCommand("open-assets", { target: added[0], types: ["image"], accept: "image/*" }); }
  }
  // Drag from our own panels: register the block once, then hand GrapesJS the native drag event.
  function dragStart(id: string, content: BlockProperties["content"], label: string, ev: DragEvent, activate?: boolean) {
    const ed = edRef.current; const bm = ed?.Blocks; if (!ed || !bm) return;
    const b = bm.get(id) || bm.add(id, { label, content, activate, select: true });
    bm.startDrag(b, ev);
  }
  const dragEnd = () => edRef.current?.Blocks.endDrag();

  function setSimple(key: keyof Simple, value: string | boolean | null) {
    const c = edRef.current?.getSelected(); if (!c) return;
    c.setStyle(applySimple({ ...(c.getStyle() as Style) }, key, value));
    setSel((s) => (s ? { ...s, style: { ...(c.getStyle() as Style) } } : s));
  }
  const simple = sel ? readSimple(sel.style) : null;

  return (
    <div className="tk-design flex-1 min-h-0 flex">
      <aside className="w-64 shrink-0 bg-white border-r border-slate-200 overflow-y-auto" aria-label="Blocks and fields">
        <section className="p-2.5 border-b border-slate-200" aria-label="Blocks">
          <button type="button" aria-expanded={layoutOpen} onClick={() => setLayoutOpen((o) => !o)} className="w-full flex items-center gap-1.5 text-2xs font-semibold text-ink-secondary mb-1.5">
            {layoutOpen ? <ChevronDown size={13} /> : <ChevronRight size={13} />}<LayoutTemplate size={13} /> Blocks
          </button>
          {layoutOpen && (
            <div className="grid grid-cols-3 gap-1.5">
              {LAYOUT.map((d) => (
                <button key={d.id} type="button" draggable onDragStart={(e) => dragStart(`tk-${d.id}`, d.content, d.label, e.nativeEvent, d.activate)} onDragEnd={dragEnd}
                  onClick={() => addBlock(d.content, d.activate)} title="Drag onto the page, or click to add"
                  className="flex flex-col items-center justify-center gap-1 h-16 rounded-xl border border-slate-200 bg-white text-ink-secondary hover:border-primary hover:text-primary cursor-grab active:cursor-grabbing">
                  {d.icon}<span className="text-2xs font-semibold text-center leading-tight px-1">{d.label}</span>
                </button>
              ))}
            </div>
          )}
        </section>
        <FieldsPanel groups={groups} onPick={(tok) => (bindInsertCall(edRef, tok))}
          onDragField={(tok, e) => dragStart(`tk-var-${tok}`, designChip(tok), tok, e.nativeEvent)} />
      </aside>

      <div className="flex-1 min-w-0 flex flex-col">
        <div role="toolbar" aria-label="Design tools" className="px-3 py-1.5 bg-white border-b border-slate-200 flex items-center gap-1 shrink-0">
          <IconBtn label="Undo" onClick={() => edRef.current?.UndoManager.undo()}><Undo2 size={16} /></IconBtn>
          <IconBtn label="Redo" onClick={() => edRef.current?.UndoManager.redo()}><Redo2 size={16} /></IconBtn>
          <span className="w-px h-5 bg-slate-200 mx-1" aria-hidden />
          <TextBtn label="Outlines" on={outlines} onClick={() => {
            const ed = edRef.current; if (!ed) return;
            if (outlines) ed.stopCommand("core:component-outline"); else ed.runCommand("core:component-outline");
            setOutlines(!outlines);
          }}><SquareDashedBottom size={15} /></TextBtn>
          <TextBtn label="Delete" disabled={!sel} onClick={() => { const s = edRef.current?.getSelected(); if (s) s.remove(); }}><Trash2 size={15} /></TextBtn>
          <span className="ml-2 text-2xs text-ink-tertiary truncate">{sel ? `Selected: ${sel.name}` : "Click an element to select it. Double-click text to type."}</span>
        </div>
        <div className="relative flex-1 min-h-0 bg-slate-100">
          {!ready && <div className="absolute inset-0 grid place-items-center text-xs text-ink-tertiary">Opening the designer…</div>}
          <div ref={canvasEl} className="tk-design-canvas h-full" />
        </div>
      </div>

      <aside className="w-72 shrink-0 bg-white border-l border-slate-200 flex flex-col overflow-y-auto" aria-label="Style">
        <div className="px-3 py-2.5 border-b border-slate-200 text-xs font-semibold text-ink-primary">{sel ? `Style: ${sel.name}` : "Style"}</div>
        {!sel && <p className="px-3 py-3 text-2xs text-ink-tertiary">Select something on the page to change its font, size, colour, alignment and spacing.</p>}
        {sel && simple && (
          <div className="p-3 space-y-3.5" data-testid="simple-style">
            <Row label="Font">
              <select aria-label="Font" value={simple.font || ""} onChange={(e) => setSimple("font", e.target.value || null)} className="glass-input w-full rounded-lg px-2 py-1.5 text-xs">
                <option value="">Same as the page</option>
                {FONTS.map((f) => <option key={f} value={f}>{f}</option>)}
              </select>
            </Row>
            <Row label="Size">
              <Seg value={simple.size} onPick={(v) => setSimple("size", v)} options={SIZES.map(([k, l]) => [k, l])} />
            </Row>
            <Row label="Bold">
              <button type="button" aria-pressed={simple.bold} onClick={() => setSimple("bold", !simple.bold)}
                className={`h-8 px-3 rounded-lg border text-xs font-semibold inline-flex items-center gap-1 ${simple.bold ? "border-primary bg-primary/10 text-primary" : "border-slate-200 text-ink-secondary hover:bg-slate-50"}`}><Bold size={14} /> Bold</button>
            </Row>
            <Row label="Colour">
              <div className="flex items-center gap-1.5">
                {COLOURS.map((c) => (
                  <button key={c} type="button" aria-label={`Colour ${c}`} aria-pressed={simple.colour === c} onClick={() => setSimple("colour", c)}
                    className={`w-6 h-6 rounded-full border ${simple.colour === c ? "ring-2 ring-primary ring-offset-1" : "border-slate-300"}`} style={{ background: c }} />
                ))}
                <label className="w-6 h-6 rounded-full border border-dashed border-slate-300 relative cursor-pointer" title="Other colour">
                  <input type="color" aria-label="Other colour" value={/^#[0-9a-f]{6}$/i.test(simple.colour || "") ? simple.colour! : "#111111"} onChange={(e) => setSimple("colour", e.target.value)} className="absolute inset-0 opacity-0 cursor-pointer" />
                </label>
              </div>
            </Row>
            <Row label="Alignment">
              <Seg value={simple.align} onPick={(v) => setSimple("align", v)} options={ALIGN.map((a) => [a, a === "left" ? <AlignLeft key={a} size={14} aria-label="Left" /> : a === "center" ? <AlignCenter key={a} size={14} aria-label="Center" /> : <AlignRight key={a} size={14} aria-label="Right" />])} />
            </Row>
            <Row label="Spacing">
              <Seg value={simple.spacing} onPick={(v) => setSimple("spacing", v)} options={SPACING.map(([k, l]) => [k, l])} />
            </Row>
          </div>
        )}
        <div className={sel ? "border-t border-slate-200" : "hidden"}>
          <button type="button" aria-expanded={more} onClick={() => setMore((m) => !m)} className="w-full flex items-center gap-1.5 px-3 py-2.5 text-xs font-semibold text-ink-secondary hover:text-ink-primary">
            {more ? <ChevronDown size={14} /> : <ChevronRight size={14} />} More styles
          </button>
          <div className={more ? "" : "hidden"}>
            <div ref={traitEl} className={`tk-gjs-traits ${sel?.traits ? "" : "hidden"}`} />
            <div ref={styleEl} className="tk-gjs-styles" />
          </div>
        </div>
      </aside>
    </div>
  );
}

function bindInsertCall(ed: { current: Editor | null }, tok: string) {
  const e = ed.current; if (!e) return;
  const rte = e.RichTextEditor; const s = e.getSelected();
  if (s && e.getEditing() && rte?.globalRte) { rte.globalRte.insertHTML(designChip(tok)); return; }
  const parent = s?.parent();
  const added = (s && parent ? parent.append(designChip(tok, true), { at: s.index() + 1 }) : e.getWrapper()?.append(designChip(tok, true))) as Component[] | undefined;
  if (added?.[0]) e.select(added[0]);
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return <div><div className="text-2xs font-semibold text-ink-tertiary mb-1">{label}</div>{children}</div>;
}
function Seg({ value, onPick, options }: { value: string | null; onPick: (v: string | null) => void; options: [string, ReactNode][] }) {
  return (
    <div className="inline-flex rounded-lg bg-slate-100 p-0.5 gap-0.5" role="group">
      {options.map(([k, l]) => (
        <button key={k} type="button" aria-pressed={value === k} onClick={() => onPick(value === k ? null : k)}
          className={`h-7 px-2.5 rounded-md text-xs font-semibold inline-flex items-center ${value === k ? "bg-white text-primary shadow-2xs" : "text-ink-secondary hover:text-ink-primary"}`}>{l}</button>
      ))}
    </div>
  );
}
function IconBtn({ label, onClick, children, on, disabled }: { label: string; onClick: () => void; children: ReactNode; on?: boolean; disabled?: boolean }) {
  return (
    <button type="button" aria-label={label} title={label} aria-pressed={on} disabled={disabled} onClick={onClick}
      className={`w-8 h-8 rounded-lg grid place-items-center disabled:opacity-40 ${on ? "bg-primary/10 text-primary" : "text-ink-secondary hover:bg-slate-100 hover:text-ink-primary"}`}>{children}</button>
  );
}
function TextBtn({ label, onClick, children, on, disabled }: { label: string; onClick: () => void; children: ReactNode; on?: boolean; disabled?: boolean }) {
  return (
    <button type="button" aria-pressed={on} disabled={disabled} onClick={onClick}
      className={`h-8 px-2.5 rounded-lg inline-flex items-center gap-1.5 text-xs font-semibold disabled:opacity-40 ${on ? "bg-primary/10 text-primary" : "text-ink-secondary hover:bg-slate-100 hover:text-ink-primary"}`}>{children}{label}</button>
  );
}
