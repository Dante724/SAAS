// "Text" view: a word-processor style editor (Tiptap, MIT) on a page-sized sheet.
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { EditorContent, useEditor, useEditorState, type Editor } from "@tiptap/react";
import {
  AlignCenter, AlignLeft, AlignRight, BetweenHorizontalEnd, BetweenVerticalEnd, Bold, Heading1, Heading2, Heading3, ImageIcon, Italic, Link2, List, ListOrdered,
  Pilcrow, Redo2, Table, Trash2, Underline, Undo2, Unlink,
} from "lucide-react";
import { fromTextView, textExtensions, toTextView } from "./tiptapSchema";
import { scopeCss, styleText } from "./templateHtml";
import VariableMenu, { type VarGroup } from "./VariableMenu";
import { buttonClass } from "../../../components/Button";
import type { PageBox } from "./types";
import "./templateEditor.css";

function withAlign(style: string | null, align: string): string {
  const rest = (style || "").split(";").map((d) => d.trim()).filter((d) => d && !/^text-align\s*:/i.test(d));
  return [...rest, `text-align: ${align}`].join("; ");
}
function setAlign(editor: Editor, align: "left" | "center" | "right") {
  const { state, view } = editor;
  const { from, to } = state.selection;
  const tr = state.tr;
  state.doc.nodesBetween(from, to, (node, pos) => {
    if (node.isTextblock && "style" in node.attrs) { tr.setNodeMarkup(pos, undefined, { ...node.attrs, style: withAlign(node.attrs.style, align) }); return false; }
    return true;
  });
  view.dispatch(tr);
  editor.commands.focus();
}

function Btn({ on, label, onClick, children, disabled }: { on?: boolean; label: string; onClick: () => void; children: ReactNode; disabled?: boolean }) {
  return (
    <button type="button" aria-label={label} title={label} aria-pressed={on} disabled={disabled}
      onMouseDown={(e) => e.preventDefault()} onClick={onClick}
      className={`w-8 h-8 rounded-lg grid place-items-center transition-colors disabled:opacity-40 ${on ? "bg-primary/10 text-primary" : "text-ink-secondary hover:bg-slate-100 hover:text-ink-primary"}`}>
      {children}
    </button>
  );
}
const TxtBtn = ({ label, onClick }: { label: string; onClick: () => void }) => (
  <button type="button" onMouseDown={(e) => e.preventDefault()} onClick={onClick} className="h-7 px-2 rounded-md text-2xs font-semibold text-ink-secondary hover:bg-white hover:text-ink-primary">{label}</button>
);
const Sep = () => <span className="w-px h-5 bg-slate-200 mx-1" aria-hidden />;

/** Small inline form for a link or image address. */
function UrlPop({ label, icon, placeholder, onApply, extra }: { label: string; icon: ReactNode; placeholder: string; onApply: (url: string) => void; extra?: (close: () => void) => ReactNode }) {
  const [open, setOpen] = useState(false);
  const [v, setV] = useState("");
  return (
    <div className="relative">
      <Btn label={label} on={open} onClick={() => setOpen((o) => !o)}>{icon}</Btn>
      {open && (
        <form className="absolute left-0 top-9 z-30 w-72 bg-white border border-slate-200 rounded-xl shadow-drawer p-2 space-y-2"
          onSubmit={(e) => { e.preventDefault(); if (v.trim()) onApply(v.trim()); setV(""); setOpen(false); }}>
          <input autoFocus value={v} onChange={(e) => setV(e.target.value)} placeholder={placeholder} aria-label={label}
            className="glass-input w-full rounded-lg px-2.5 py-1.5 text-xs" />
          <div className="flex items-center gap-2">
            <button type="submit" className={buttonClass("primary", "sm")}>Add</button>
            {extra?.(() => setOpen(false))}
          </div>
        </form>
      )}
    </div>
  );
}

export default function TextMode({ html, onChange, groups, page, bindInsert }: {
  html: string; onChange: (html: string) => void; groups: VarGroup[]; page: PageBox;
  bindInsert?: (fn: ((tok: string) => void) | null) => void;
}) {
  // Load once per mount; the parent remounts this view when the template changes elsewhere.
  const start = useMemo(() => toTextView(html), []); // eslint-disable-line react-hooks/exhaustive-deps
  const styles = useRef(start.styles);
  const editor = useEditor({
    extensions: textExtensions("Type your document, or insert a variable…"),
    content: start.body,
    editorProps: { attributes: { class: "tk-text-page outline-none", "aria-label": "Document text", spellcheck: "true" } },
    onUpdate: ({ editor: ed }) => onChange(fromTextView(styles.current, ed.getHTML())),
  });
  const st = useEditorState({
    editor,
    selector: ({ editor: e }) => e && ({
      bold: e.isActive("bold"), italic: e.isActive("italic"), underline: e.isActive("underline"),
      h1: e.isActive("heading", { level: 1 }), h2: e.isActive("heading", { level: 2 }), h3: e.isActive("heading", { level: 3 }),
      bullet: e.isActive("bulletList"), ordered: e.isActive("orderedList"), link: e.isActive("link"), table: e.isActive("table"),
      undo: e.can().undo(), redo: e.can().redo(),
    }),
  });
  const insertTok = (tok: string) => editor?.chain().focus().insertContent({ type: "variable", attrs: { tok } }).run();
  useEffect(() => { bindInsert?.(editor ? insertTok : null); return () => bindInsert?.(null); }); // eslint-disable-line react-hooks/exhaustive-deps

  const css = useMemo(() => scopeCss(styleText(styles.current), ".tk-text-sheet"), []);
  if (!editor || !st) return null;
  const c = () => editor.chain().focus();

  return (
    <div className="flex-1 min-h-0 flex flex-col">
      <div role="toolbar" aria-label="Text formatting" className="px-3 py-1.5 bg-white border-b border-slate-200 flex items-center gap-0.5 flex-wrap shrink-0">
        <Btn label="Undo" disabled={!st.undo} onClick={() => c().undo().run()}><Undo2 size={16} /></Btn>
        <Btn label="Redo" disabled={!st.redo} onClick={() => c().redo().run()}><Redo2 size={16} /></Btn>
        <Sep />
        <Btn label="Paragraph" onClick={() => c().setParagraph().run()}><Pilcrow size={16} /></Btn>
        <Btn label="Heading 1" on={st.h1} onClick={() => c().toggleHeading({ level: 1 }).run()}><Heading1 size={16} /></Btn>
        <Btn label="Heading 2" on={st.h2} onClick={() => c().toggleHeading({ level: 2 }).run()}><Heading2 size={16} /></Btn>
        <Btn label="Heading 3" on={st.h3} onClick={() => c().toggleHeading({ level: 3 }).run()}><Heading3 size={16} /></Btn>
        <Sep />
        <Btn label="Bold" on={st.bold} onClick={() => c().toggleBold().run()}><Bold size={16} /></Btn>
        <Btn label="Italic" on={st.italic} onClick={() => c().toggleItalic().run()}><Italic size={16} /></Btn>
        <Btn label="Underline" on={st.underline} onClick={() => c().toggleUnderline().run()}><Underline size={16} /></Btn>
        <Sep />
        <Btn label="Bullet list" on={st.bullet} onClick={() => c().toggleBulletList().run()}><List size={16} /></Btn>
        <Btn label="Numbered list" on={st.ordered} onClick={() => c().toggleOrderedList().run()}><ListOrdered size={16} /></Btn>
        <Btn label="Align left" onClick={() => setAlign(editor, "left")}><AlignLeft size={16} /></Btn>
        <Btn label="Align center" onClick={() => setAlign(editor, "center")}><AlignCenter size={16} /></Btn>
        <Btn label="Align right" onClick={() => setAlign(editor, "right")}><AlignRight size={16} /></Btn>
        <Sep />
        <Btn label="Insert table" onClick={() => c().insertTable({ rows: 3, cols: 3, withHeaderRow: false }).run()}><Table size={16} /></Btn>
        {st.table && (
          <span className="inline-flex items-center gap-0.5 rounded-lg bg-slate-100 px-1" role="group" aria-label="Table">
            <Btn label="Add row below" onClick={() => c().addRowAfter().run()}><BetweenHorizontalEnd size={16} /></Btn>
            <Btn label="Add column right" onClick={() => c().addColumnAfter().run()}><BetweenVerticalEnd size={16} /></Btn>
            <TxtBtn label="Delete row" onClick={() => c().deleteRow().run()} />
            <TxtBtn label="Delete column" onClick={() => c().deleteColumn().run()} />
            <Btn label="Delete table" onClick={() => c().deleteTable().run()}><Trash2 size={16} /></Btn>
          </span>
        )}
        <UrlPop label="Insert image" icon={<ImageIcon size={16} />} placeholder="Image address (https://…)"
          onApply={(src) => c().setImage({ src }).run()}
          extra={(close) => <button type="button" onClick={() => { insertTok("{Company Logo}"); close(); }} className="text-xs font-semibold text-primary hover:underline">Use the shop logo</button>} />
        {st.link
          ? <Btn label="Remove link" on onClick={() => c().unsetLink().run()}><Unlink size={16} /></Btn>
          : <UrlPop label="Add link" icon={<Link2 size={16} />} placeholder="Link address (https://…)" onApply={(href) => c().setLink({ href }).run()} />}
        <Sep />
        <VariableMenu groups={groups} onPick={insertTok} />
      </div>
      <div className="flex-1 min-h-0 overflow-auto bg-slate-100">
        <div className="min-h-full flex justify-center py-8 px-6">
          {css && <style>{css}</style>}
          <div className="tk-text-sheet tk-paper bg-paper shadow-lg"
            style={{ width: page.w, maxWidth: "100%", minHeight: page.minH, padding: page.pad, fontFamily: `${page.font}, Arial, sans-serif` }}>
            <EditorContent editor={editor} />
          </div>
        </div>
      </div>
    </div>
  );
}
