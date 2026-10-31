// Signature capture in the shop — a canvas you draw on with a finger, pen or mouse (pointer
// events cover all three). The pad is a white "paper" surface in both themes so the saved PNG
// (dark ink) prints and shows the same everywhere.
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { M } from "./Shell";

const PAPER = "#ffffff";
const INK = "#18254a";

export default function SignaturePad({ title = "Customer signature", hint = "Ask the customer to sign in the box.", busy, onSave, onClose }: {
  title?: string; hint?: string; busy?: boolean; onSave: (dataUrl: string, note: string) => void; onClose: () => void;
}) {
  const ref = useRef<HTMLCanvasElement | null>(null);
  const drawing = useRef(false);
  const [dirty, setDirty] = useState(false);
  const [note, setNote] = useState("");

  useEffect(() => {
    const c = ref.current; if (!c) return;
    c.width = c.offsetWidth || 420; c.height = 180;
    const ctx = c.getContext?.("2d"); if (!ctx) return;
    ctx.strokeStyle = INK; ctx.lineWidth = 2.2; ctx.lineCap = "round"; ctx.lineJoin = "round";
  }, []);

  const pos = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const c = e.currentTarget; const r = c.getBoundingClientRect();
    return { x: (e.clientX - r.left) * (c.width / (r.width || 1)), y: (e.clientY - r.top) * (c.height / (r.height || 1)) };
  };
  function down(e: React.PointerEvent<HTMLCanvasElement>) {
    const ctx = e.currentTarget.getContext("2d"); if (!ctx) return;
    e.preventDefault(); e.currentTarget.setPointerCapture?.(e.pointerId);
    drawing.current = true; setDirty(true);
    const p = pos(e); ctx.beginPath(); ctx.moveTo(p.x, p.y); ctx.lineTo(p.x + 0.1, p.y + 0.1); ctx.stroke();
  }
  function move(e: React.PointerEvent<HTMLCanvasElement>) {
    if (!drawing.current) return;
    const ctx = e.currentTarget.getContext("2d"); if (!ctx) return;
    e.preventDefault(); const p = pos(e); ctx.lineTo(p.x, p.y); ctx.stroke();
  }
  const up = () => { drawing.current = false; };
  function clear() {
    const c = ref.current; c?.getContext("2d")?.clearRect(0, 0, c.width, c.height); setDirty(false);
  }
  function save() {
    if (!dirty || !ref.current || busy) return;
    onSave(ref.current.toDataURL("image/png"), note.trim());
  }

  return createPortal(
    <div className="fixed inset-0 z-[60] flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-ink-primary/40" aria-hidden="true" onClick={onClose} />
      <div role="dialog" aria-label={title} className="relative bg-white rounded-2xl shadow-clay-drawer border border-slate-200 w-full max-w-lg p-5 space-y-3">
        <div className="flex items-center justify-between">
          <h4 className="text-sm font-bold text-ink-primary">{title}</h4>
          <button aria-label="Close" onClick={onClose} className="text-ink-tertiary hover:text-ink-primary"><M n="close" c="text-lg" /></button>
        </div>
        <p className="text-xs text-ink-secondary">{hint}</p>
        <canvas ref={ref} aria-label="Signature box" style={{ background: PAPER }}
          onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerLeave={up} onPointerCancel={up}
          className="block h-[180px] w-full touch-none rounded-xl border-[1.5px] border-dashed border-primary/40 cursor-crosshair" />
        <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Note (optional), e.g. Collected the phone" aria-label="Note"
          className="glass-input w-full py-2 px-3 text-xs rounded-xl" />
        <div className="flex items-center gap-2 pt-1">
          <button onClick={save} disabled={!dirty || busy} className="px-4 py-2 rounded-xl bg-primary text-white text-xs font-bold shadow-clay-sm hover:brightness-110 disabled:opacity-50">{busy ? "Saving…" : "Save signature"}</button>
          <button onClick={clear} disabled={!dirty} className="px-3.5 py-2 rounded-xl text-xs font-bold text-ink-secondary bg-white border border-slate-200 hover:bg-slate-50 disabled:opacity-50">Clear</button>
          <button onClick={onClose} className="ml-auto px-3 py-2 rounded-xl text-xs font-semibold text-ink-tertiary hover:text-ink-primary">Cancel</button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
