// 144dc — mark up an intake photo: draw circles, spots and freehand strokes on the image so a tech
// (seamstress, auto, repair) can point right at the damage. Flattens to a new JPEG and hands the data
// URL back; the caller uploads it and swaps the photo id, so the marks show everywhere the photo does.
import { useEffect, useRef, useState } from "react";
import { M } from "../../components/Shell";
import { loadFailed } from "../../lib/notice";

type Tool = "circle" | "spot" | "pen";
type Pt = { x: number; y: number };
type Stroke = { tool: Tool; color: string; width: number; points: Pt[] };

const COLORS = ["#ef4444", "#f59e0b", "#22c55e", "#3b82f6", "#ffffff"];
const MAX_DIM = 1600; // cap exported size so uploads stay small

export default function PhotoAnnotator({ src, onSave, onClose }: {
  src: string; onSave: (dataUrl: string) => Promise<void> | void; onClose: () => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const imgRef = useRef<HTMLImageElement | null>(null);
  const [strokes, setStrokes] = useState<Stroke[]>([]);
  const [tool, setTool] = useState<Tool>("circle");
  const [color, setColor] = useState(COLORS[0]);
  const [width, setWidth] = useState(6);
  const [ready, setReady] = useState(false);
  const [saving, setSaving] = useState(false);
  const drawing = useRef<Stroke | null>(null);

  // load the (authed) image onto an offscreen Image, size the canvas to it
  useEffect(() => {
    let dead = false; let obj = "";
    let tok = ""; try { tok = localStorage.getItem("ros_tok") || ""; } catch { /* ignore */ }
    fetch(src, { headers: tok ? { Authorization: `Bearer ${tok}` } : {} })
      .then((r) => (r.ok ? r.blob() : null))
      .then((b) => {
        if (!b || dead) return;
        obj = URL.createObjectURL(b);
        const img = new Image();
        img.onload = () => {
          if (dead) return;
          const scale = Math.min(1, MAX_DIM / Math.max(img.naturalWidth, img.naturalHeight));
          const cv = canvasRef.current!;
          cv.width = Math.round(img.naturalWidth * scale);
          cv.height = Math.round(img.naturalHeight * scale);
          imgRef.current = img;
          setReady(true);
        };
        img.src = obj;
      }).catch(loadFailed("the photo"));
    return () => { dead = true; if (obj) URL.revokeObjectURL(obj); };
  }, [src]);

  // repaint base image + every stroke whenever anything changes
  useEffect(() => {
    const cv = canvasRef.current, img = imgRef.current;
    if (!cv || !img || !ready) return;
    const ctx = cv.getContext("2d")!;
    ctx.clearRect(0, 0, cv.width, cv.height);
    ctx.drawImage(img, 0, 0, cv.width, cv.height);
    const all = drawing.current ? [...strokes, drawing.current] : strokes;
    for (const s of all) paint(ctx, s);
  }, [strokes, ready]);

  function paint(ctx: CanvasRenderingContext2D, s: Stroke) {
    ctx.strokeStyle = s.color; ctx.fillStyle = s.color; ctx.lineWidth = s.width;
    ctx.lineJoin = "round"; ctx.lineCap = "round";
    ctx.shadowColor = "rgba(0,0,0,0.5)"; ctx.shadowBlur = 2;
    if (s.tool === "spot") {
      const p = s.points[0]; if (!p) return;
      ctx.beginPath(); ctx.arc(p.x, p.y, s.width * 1.6, 0, Math.PI * 2); ctx.fill();
    } else if (s.tool === "circle") {
      const a = s.points[0], b = s.points[s.points.length - 1]; if (!a || !b) return;
      const rx = Math.abs(b.x - a.x), ry = Math.abs(b.y - a.y);
      ctx.beginPath(); ctx.ellipse(a.x, a.y, rx || 2, ry || 2, 0, 0, Math.PI * 2); ctx.stroke();
    } else {
      ctx.beginPath();
      s.points.forEach((p, i) => (i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)));
      ctx.stroke();
    }
    ctx.shadowBlur = 0;
  }

  function pos(e: React.PointerEvent): Pt {
    const cv = canvasRef.current!; const r = cv.getBoundingClientRect();
    return { x: ((e.clientX - r.left) / r.width) * cv.width, y: ((e.clientY - r.top) / r.height) * cv.height };
  }
  function down(e: React.PointerEvent) {
    if (!ready) return;
    (e.target as Element).setPointerCapture?.(e.pointerId);
    const p = pos(e);
    const s: Stroke = { tool, color, width, points: [p] };
    if (tool === "spot") { setStrokes((x) => [...x, s]); return; }
    drawing.current = s; repaint();
  }
  function move(e: React.PointerEvent) {
    if (!drawing.current) return;
    const p = pos(e);
    if (tool === "pen") drawing.current.points.push(p);
    else drawing.current.points = [drawing.current.points[0], p]; // circle: center + current
    repaint();
  }
  function up() {
    if (!drawing.current) return;
    const s = drawing.current; drawing.current = null;
    setStrokes((x) => [...x, s]);
  }
  function repaint() {
    const cv = canvasRef.current, img = imgRef.current; if (!cv || !img) return;
    const ctx = cv.getContext("2d")!;
    ctx.clearRect(0, 0, cv.width, cv.height); ctx.drawImage(img, 0, 0, cv.width, cv.height);
    for (const s of strokes) paint(ctx, s);
    if (drawing.current) paint(ctx, drawing.current);
  }

  async function save() {
    const cv = canvasRef.current; if (!cv) return;
    setSaving(true);
    try { await onSave(cv.toDataURL("image/jpeg", 0.9)); }
    finally { setSaving(false); }
  }

  const toolBtn = (t: Tool, icon: string, label: string) => (
    <button onClick={() => setTool(t)} title={label}
      className={`flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-xs font-bold ${tool === t ? "bg-primary text-white shadow-clay-sm" : "bg-white text-ink-secondary border border-slate-200 hover:bg-slate-50"}`}>
      <M n={icon} c="text-base" />{label}
    </button>
  );

  return (
    <div className="fixed inset-0 z-[60] bg-black/70 flex items-center justify-center p-4" role="presentation" onClick={onClose}>
      <div className="bg-white rounded-2xl shadow-clay-drawer max-w-3xl w-full max-h-[92vh] flex flex-col overflow-hidden" onClick={(e) => e.stopPropagation()}>
        <div className="px-4 py-2.5 border-b border-slate-100 flex items-center gap-2">
          <M n="edit" c="text-primary" /><h3 className="text-sm font-bold text-ink-primary">Mark up photo</h3>
          <span className="text-[11px] text-ink-tertiary">circle the damage, drop a spot, or draw</span>
          <button aria-label="Close" onClick={onClose} className="ml-auto text-ink-tertiary hover:text-ink-primary"><M n="close" /></button>
        </div>

        <div className="px-4 py-2 border-b border-slate-100 flex flex-wrap items-center gap-2">
          {toolBtn("circle", "radio_button_unchecked", "Circle")}
          {toolBtn("spot", "fiber_manual_record", "Spot")}
          {toolBtn("pen", "draw", "Draw")}
          <span className="w-px h-5 bg-slate-200 mx-1" />
          {COLORS.map((c) => (
            <button key={c} onClick={() => setColor(c)} aria-label={`color ${c}`}
              className={`w-6 h-6 rounded-full border-2 ${color === c ? "border-ink-primary scale-110" : "border-white"} shadow-clay-sm`} style={{ backgroundColor: c }} />
          ))}
          <span className="w-px h-5 bg-slate-200 mx-1" />
          <input type="range" min={2} max={16} value={width} onChange={(e) => setWidth(Number(e.target.value))} className="w-20" aria-label="Line width" />
          <button onClick={() => setStrokes((x) => x.slice(0, -1))} disabled={!strokes.length}
            className="ml-auto flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-xs font-semibold text-ink-secondary border border-slate-200 hover:bg-slate-50 disabled:opacity-40"><M n="undo" c="text-base" />Undo</button>
          <button onClick={() => setStrokes([])} disabled={!strokes.length}
            className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-xs font-semibold text-rose-700 border border-rose-200 hover:bg-rose-50 disabled:opacity-40"><M n="delete" c="text-base" />Clear</button>
        </div>

        <div className="flex-1 overflow-auto bg-slate-800 flex items-center justify-center p-3">
          {!ready && <div className="text-xs text-white/70 py-12">Loading photo…</div>}
          <canvas ref={canvasRef} onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerLeave={up}
            className={`max-w-full max-h-[60vh] object-contain touch-none rounded-lg ${ready ? "" : "hidden"}`} style={{ cursor: "crosshair" }} />
        </div>

        <div className="px-4 py-3 border-t border-slate-100 flex items-center justify-between bg-slate-50/70">
          <span className="text-[11px] text-ink-tertiary">Saved as a new marked-up photo on this order.</span>
          <div className="flex gap-2">
            <button onClick={onClose} className="px-4 py-2 rounded-lg text-xs font-bold text-ink-secondary hover:bg-slate-100">Cancel</button>
            <button onClick={save} disabled={saving || !ready} className="px-4 py-2 rounded-lg text-xs font-bold text-white bg-primary hover:opacity-90 shadow-clay-sm disabled:opacity-50">{saving ? "Saving…" : "Save markup"}</button>
          </div>
        </div>
      </div>
    </div>
  );
}
