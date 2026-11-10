// "Design" view: drag-and-drop page builder (GrapesJS core, BSD-3) on a page-sized sheet.
// Tekly draws the blocks panel itself; the style and settings panels are GrapesJS's own, restyled
// in templateEditor.css. Nothing from the library's own branding or toolbar is shown.
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import grapesjs, { type BlockProperties, type Component, type Editor } from "grapesjs";
import "grapesjs/dist/css/grapes.min.css";
import {
  ChevronDown, ChevronRight, Columns2, Heading, ImageIcon, LayoutTemplate, Minus, Redo2, Search, Signature, Sigma,
  SquareDashedBottom, Store, Table2, Trash2, Type, Undo2, Braces,
} from "lucide-react";
import { addTeklyTypes, baseConfig, designChip, exportDesign, snapshotStyles, toDesignView } from "./grapesSetup";
import { chipLabel, styleText } from "./templateHtml";
import type { VarGroup } from "./VariableMenu";
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

const SECTORS = [
  { name: "Text", open: true, properties: ["font-family", "font-size", "font-weight", "color", "text-align", "line-height"] },
  { name: "Spacing", open: false, properties: ["margin", "padding"] },
  { name: "Size", open: false, properties: ["width", "height", "max-width"] },
  { name: "Border", open: false, properties: ["border", "border-radius"] },
  { name: "Background", open: false, properties: ["background-color"] },
];

function frameCss(page: PageBox, templateCss: string) {
  return `*{box-sizing:border-box}html,body{background:#fff}body{margin:0;padding:${page.pad}px;min-height:${page.minH}px;font-family:${page.font},Arial,sans-serif;color:#111;font-size:13px;line-height:1.5}
table{border-collapse:collapse}img{max-width:100%}
.tok-pill{display:inline-block;background:#eef2ff;color:#3730a3;border:1px solid #c7d2fe;border-radius:6px;padding:0 6px;margin:0 1px;font-size:.85em;font-weight:600;line-height:1.5;white-space:nowrap;font-family:system-ui,sans-serif}
.tok-pill.tok-logic{background:#fff7ed;color:#9a3412;border-color:#fed7aa}
div.tok-pill{display:block;width:max-content;margin:2px 0}
${templateCss}`;
}

export default function DesignMode({ html, onChange, groups, page, bindInsert, bindFlush }: {
  html: string; onChange: (html: string) => void; groups: VarGroup[]; page: PageBox;
  bindInsert?: (fn: ((tok: string) => void) | null) => void;
  /** Gets a function that finishes any text being typed and returns the latest HTML (null = unchanged). */
  bindFlush?: (fn: (() => Promise<string | null>) | null) => void;
}) {
  const canvasEl = useRef<HTMLDivElement | null>(null);
  const styleEl = useRef<HTMLDivElement | null>(null);
  const traitEl = useRef<HTMLDivElement | null>(null);
  const edRef = useRef<Editor | null>(null);
  const change = useRef(onChange); change.current = onChange;
  const [ready, setReady] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [outlines, setOutlines] = useState(true);
  const [q, setQ] = useState("");
  const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({ Layout: true });

  useEffect(() => {
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
      canvasCss: ".gjs-selected{outline:2px solid #0059ba !important;outline-offset:-2px}.gjs-hovered{outline:1px solid #2c72d9 !important}",
      assetManager: { embedAsBase64: true, upload: false },
      richTextEditor: { actions: ["bold", "italic", "underline", "link"] },
    });
    edRef.current = editor;
    let snap = new Map<string, string>();
    let initial: string | null = null;
    editor.on("load", () => {
      snap = snapshotStyles(editor);
      initial = exportDesign(editor, load, snap);
      editor.runCommand("core:component-outline");
      setReady(true);
    });
    const latest = () => {
      if (initial === null) return null;
      const out = exportDesign(editor, load, snap);
      return out !== initial ? out : null;
    };
    editor.on("update", () => { const out = latest(); if (out !== null) change.current(out); });
    // Text being typed lives in the canvas until editing ends; end it before anyone reads the HTML.
    bindFlush?.(async () => {
      const sel = editor.getSelected() as any;
      if (editor.getEditing() && sel?.view?.disableEditing) await sel.view.disableEditing();
      return latest();
    });
    editor.on("component:selected component:deselected", () => {
      const c = editor.getSelected();
      // The made-up element id isn't something to edit; title, alt text and link address are.
      if (c && c.getTrait("id")) c.removeTrait("id");
      setSelected(c ? (c.getName() || c.get("type") || "Element") : null);
    });
    return () => { bindFlush?.(null); edRef.current = null; editor.destroy(); };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Insert a placeholder: into the text being edited, after the selected element, or at the end.
  useEffect(() => {
    bindInsert?.((tok: string) => {
      const ed = edRef.current; if (!ed) return;
      const rte = ed.RichTextEditor;
      const sel = ed.getSelected();
      if (sel && ed.getEditing() && rte?.globalRte) { rte.globalRte.insertHTML(designChip(tok)); return; }
      const parent = sel?.parent();
      if (sel && parent) parent.append(designChip(tok, true), { at: sel.index() + 1 });
      else ed.getWrapper()?.append(designChip(tok, true));
    });
    return () => bindInsert?.(null);
  }, [bindInsert]);

  function addBlock(content: BlockProperties["content"], activate?: boolean) {
    const ed = edRef.current; if (!ed) return;
    const sel = ed.getSelected();
    const parent = sel?.parent();
    const added = (sel && parent ? parent.append(content as any, { at: sel.index() + 1 }) : ed.getWrapper()!.append(content as any)) as Component[];
    if (added?.[0]) { ed.select(added[0]); if (activate) ed.runCommand("open-assets", { target: added[0], types: ["image"], accept: "image/*" }); }
  }
  // GrapesJS drag from our own panel: register blocks once, then hand it the native drag event.
  function blockFor(id: string, content: BlockProperties["content"], label: string, activate?: boolean) {
    const bm = edRef.current?.Blocks; if (!bm) return null;
    return bm.get(id) || bm.add(id, { label, content, activate, select: true });
  }
  const dragStart = (id: string, content: BlockProperties["content"], label: string, ev: DragEvent, activate?: boolean) => {
    const ed = edRef.current; const b = blockFor(id, content, label, activate);
    if (ed && b) ed.Blocks.startDrag(b, ev);
  };
  const dragEnd = () => edRef.current?.Blocks.endDrag();

  const ql = q.trim().toLowerCase();
  const varGroups = useMemo(() => groups.map((g) => ({ ...g, items: g.items.filter((t) => !ql || t.toLowerCase().includes(ql)) })).filter((g) => g.items.length), [groups, ql]);
  const layout = LAYOUT.filter((b) => !ql || b.label.toLowerCase().includes(ql));
  const isOpen = (g: string) => !!ql || !!openGroups[g];
  const toggle = (g: string) => setOpenGroups((o) => ({ ...o, [g]: !isOpen(g) }));

  const Tile = ({ d }: { d: Def }) => (
    <button type="button" draggable onDragStart={(e) => dragStart(`tk-${d.id}`, d.content, d.label, e.nativeEvent, d.activate)} onDragEnd={dragEnd}
      onClick={() => addBlock(d.content, d.activate)} title={`Drag onto the page, or click to add`}
      className="flex flex-col items-center justify-center gap-1 h-[68px] rounded-xl border border-slate-200 bg-white text-ink-secondary hover:border-primary hover:text-primary cursor-grab active:cursor-grabbing">
      {d.icon}<span className="text-2xs font-semibold text-center leading-tight px-1">{d.label}</span>
    </button>
  );

  return (
    <div className="tk-design flex-1 min-h-0 flex">
      <aside className="w-64 shrink-0 bg-white border-r border-slate-200 flex flex-col" aria-label="Blocks">
        <div className="p-2.5 border-b border-slate-200">
          <label className="flex items-center gap-1.5 px-2 h-8 rounded-lg border border-slate-200 bg-white focus-within:border-primary">
            <Search size={14} className="text-ink-tertiary" aria-hidden />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search blocks and variables" aria-label="Search blocks and variables" className="flex-1 bg-transparent text-xs text-ink-primary outline-none" />
          </label>
        </div>
        <div className="flex-1 overflow-y-auto p-2.5 space-y-3">
          {layout.length > 0 && (
            <section>
              <button type="button" onClick={() => toggle("Layout")} className="w-full flex items-center gap-1.5 text-2xs font-semibold text-ink-secondary mb-1.5">
                {isOpen("Layout") ? <ChevronDown size={13} /> : <ChevronRight size={13} />}<LayoutTemplate size={13} /> Layout
              </button>
              {isOpen("Layout") && <div className="grid grid-cols-3 gap-1.5">{layout.map((d) => <Tile key={d.id} d={d} />)}</div>}
            </section>
          )}
          <div className="text-2xs text-ink-tertiary">Variables: drag one into the page or into a line of text.</div>
          {varGroups.map((g) => (
            <section key={g.group}>
              <button type="button" onClick={() => toggle(g.group)} className="w-full flex items-center gap-1.5 text-2xs font-semibold text-ink-secondary mb-1">
                {isOpen(g.group) ? <ChevronDown size={13} /> : <ChevronRight size={13} />}<Braces size={13} /> {g.group}
                <span className="ml-auto font-normal text-ink-tertiary">{g.items.length}</span>
              </button>
              {isOpen(g.group) && (
                <div className="flex flex-wrap gap-1">
                  {g.items.map((tok) => (
                    <button key={tok} type="button" draggable onDragStart={(e) => dragStart(`tk-var-${tok}`, designChip(tok), chipLabel(tok), e.nativeEvent)} onDragEnd={dragEnd}
                      onClick={() => addBlock(designChip(tok, true))} title="Drag into the page"
                      className="tk-var-chip cursor-grab active:cursor-grabbing">{chipLabel(tok)}</button>
                  ))}
                </div>
              )}
            </section>
          ))}
        </div>
      </aside>

      <div className="flex-1 min-w-0 flex flex-col">
        <div role="toolbar" aria-label="Design tools" className="px-3 py-1.5 bg-white border-b border-slate-200 flex items-center gap-1 shrink-0">
          <IconBtn label="Undo" onClick={() => edRef.current?.UndoManager.undo()}><Undo2 size={16} /></IconBtn>
          <IconBtn label="Redo" onClick={() => edRef.current?.UndoManager.redo()}><Redo2 size={16} /></IconBtn>
          <span className="w-px h-5 bg-slate-200 mx-1" aria-hidden />
          <IconBtn label="Show outlines" on={outlines} onClick={() => {
            const ed = edRef.current; if (!ed) return;
            if (outlines) ed.stopCommand("core:component-outline"); else ed.runCommand("core:component-outline");
            setOutlines(!outlines);
          }}><SquareDashedBottom size={16} /></IconBtn>
          <IconBtn label="Delete selected" disabled={!selected} onClick={() => { const ed = edRef.current; const s = ed?.getSelected(); if (s) s.remove(); }}><Trash2 size={16} /></IconBtn>
          <span className="ml-2 text-2xs text-ink-tertiary">{selected ? `Selected: ${selected}` : "Click an element on the page to select it. Double-click text to edit it."}</span>
        </div>
        <div className="relative flex-1 min-h-0 bg-slate-100">
          {!ready && <div className="absolute inset-0 grid place-items-center text-xs text-ink-tertiary">Opening the designer…</div>}
          <div ref={canvasEl} className="tk-design-canvas h-full" />
        </div>
      </div>

      <aside className="w-72 shrink-0 bg-white border-l border-slate-200 flex flex-col overflow-y-auto" aria-label="Element settings">
        <div className="px-3 py-2.5 border-b border-slate-200 text-xs font-semibold text-ink-primary">{selected || "Nothing selected"}</div>
        {!selected && <p className="px-3 py-3 text-2xs text-ink-tertiary">Select an element to change its text, spacing, size, border and background.</p>}
        <div className={selected ? "" : "hidden"}>
          <div className="px-3 pt-3 text-2xs font-semibold text-ink-tertiary">Settings</div>
          <div ref={traitEl} className="tk-gjs-traits" />
          <div className="px-3 pt-3 text-2xs font-semibold text-ink-tertiary">Style</div>
          <div ref={styleEl} className="tk-gjs-styles" />
        </div>
      </aside>
    </div>
  );
}

function IconBtn({ label, onClick, children, on, disabled }: { label: string; onClick: () => void; children: ReactNode; on?: boolean; disabled?: boolean }) {
  return (
    <button type="button" aria-label={label} title={label} aria-pressed={on} disabled={disabled} onClick={onClick}
      className={`w-8 h-8 rounded-lg grid place-items-center disabled:opacity-40 ${on ? "bg-primary/10 text-primary" : "text-ink-secondary hover:bg-slate-100 hover:text-ink-primary"}`}>{children}</button>
  );
}
