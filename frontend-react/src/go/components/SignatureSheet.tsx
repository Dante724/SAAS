// Customer signature: a near-full-height sheet in portrait (iOS web apps can't lock to landscape).
// Smooth strokes with pointer events; the canvas uses touch-action:none so drawing never scrolls.
import { useEffect, useRef, useState } from "react";
import { Eraser } from "lucide-react";
import { api } from "../../api/client";
import { PrimaryButton, Sheet } from "./ui";
import { haptic } from "../lib/scanner";
import { useToast } from "../lib/useGo";

export default function SignatureSheet({ open, onClose, orderId, customer, onSaved }: {
  open: boolean; onClose: () => void; orderId: string; customer?: string | null; onSaved: () => void;
}) {
  const toast = useToast();
  const canvas = useRef<HTMLCanvasElement>(null);
  const drawing = useRef(false);
  const last = useRef<{ x: number; y: number } | null>(null);
  const [inked, setInked] = useState(false);
  const [busy, setBusy] = useState(false);

  // size the canvas to its box at the device's pixel density once the sheet has opened
  useEffect(() => {
    if (!open) return;
    setInked(false);
    const t = setTimeout(() => {
      const c = canvas.current;
      if (!c) return;
      const r = c.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      c.width = Math.round(r.width * dpr); c.height = Math.round(r.height * dpr);
      const ctx = c.getContext("2d")!;
      ctx.scale(dpr, dpr);
      ctx.lineCap = "round"; ctx.lineJoin = "round"; ctx.lineWidth = 2.6;
      ctx.strokeStyle = "#14213f";
    }, 260);
    return () => clearTimeout(t);
  }, [open]);

  const pos = (e: React.PointerEvent) => {
    const r = canvas.current!.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  };
  const down = (e: React.PointerEvent) => {
    canvas.current?.setPointerCapture(e.pointerId);
    drawing.current = true; last.current = pos(e);
  };
  const move = (e: React.PointerEvent) => {
    if (!drawing.current || !last.current) return;
    const ctx = canvas.current!.getContext("2d")!;
    const p = pos(e);
    const mid = { x: (last.current.x + p.x) / 2, y: (last.current.y + p.y) / 2 };
    ctx.beginPath(); ctx.moveTo(last.current.x, last.current.y); ctx.quadraticCurveTo(last.current.x, last.current.y, mid.x, mid.y); ctx.lineTo(p.x, p.y); ctx.stroke();
    last.current = p;
    if (!inked) setInked(true);
  };
  const up = () => { drawing.current = false; last.current = null; };

  function clear() {
    const c = canvas.current;
    if (!c) return;
    c.getContext("2d")!.clearRect(0, 0, c.width, c.height);
    setInked(false);
  }

  /** The ink on a white background (a transparent PNG reads as black-on-black in dark viewers). */
  function exportPng(): string {
    const c = canvas.current!;
    const out = document.createElement("canvas");
    out.width = c.width; out.height = c.height;
    const ctx = out.getContext("2d")!;
    ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, out.width, out.height);
    ctx.drawImage(c, 0, 0);
    return out.toDataURL("image/png");
  }

  async function save() {
    if (!inked || busy) return;
    setBusy(true);
    try {
      await api.post(`/orders/${encodeURIComponent(orderId)}/signature`, { signature: exportPng(), note: "Signed on Tekly Go" });
      haptic();
      toast({ title: "Signature saved" });
      onSaved();
      onClose();
    } catch (e: any) {
      toast({ title: "Couldn't save the signature", body: e?.message, tone: "error" });
    } finally { setBusy(false); }
  }

  return (
    <Sheet open={open} onClose={onClose} title="Customer signature">
      <div className="px-2">
        <p className="mb-2 text-[0.9375rem] text-ink-secondary">{customer ? `${customer}, please` : "Please"} sign below to confirm.</p>
        <div className="relative rounded-2xl border border-slate-200 bg-white" style={{ height: "min(46vh, 22rem)" }}>
          <canvas ref={canvas} className="absolute inset-0 h-full w-full rounded-2xl" style={{ touchAction: "none", background: "#fff" }}
            onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={up} onPointerLeave={up} aria-label="Signature pad" />
          {!inked && <p className="pointer-events-none absolute inset-x-0 bottom-10 text-center text-[0.875rem] text-slate-400">Sign here</p>}
          <div className="pointer-events-none absolute inset-x-6 bottom-8 border-b border-slate-300" />
        </div>
        <div className="mt-3 flex gap-2">
          <button onClick={clear} className="min-h-[3.25rem] px-5 rounded-2xl border border-slate-200 bg-white font-semibold text-ink-primary inline-flex items-center gap-2"><Eraser size={18} />Clear</button>
          <PrimaryButton onClick={save} disabled={!inked || busy} className="flex-1">{busy ? "Saving…" : "Done"}</PrimaryButton>
        </div>
      </div>
    </Sheet>
  );
}
