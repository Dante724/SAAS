import { useLayoutEffect, useRef, useState } from "react";
import type React from "react";
import { createPortal } from "react-dom";

// A small menu drawn above the page (portal + fixed position), so cards with
// overflow-hidden can't clip it. Opens below its button, or above when there
// isn't room; closes on an outside click, Escape, page scroll or resize.
export default function FloatingMenu({ anchorSel, onClose, children, width = 224 }: { anchorSel: string; onClose: () => void; children: React.ReactNode; width?: number }) {
  const popRef = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ left: number; top?: number; bottom?: number; maxH: number } | null>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useLayoutEffect(() => {
    const close = () => closeRef.current();
    const cleanup = { current: null as null | (() => void) };
    const a = document.querySelector(anchorSel) as HTMLElement | null;
    if (!a) return;
    const r = a.getBoundingClientRect();
    const W = width, gap = 4, vh = window.innerHeight;
    const left = Math.max(8, Math.min(r.left, window.innerWidth - W - 8));
    const below = vh - r.bottom - gap - 8, above = r.top - gap - 8;
    setPos(below >= 220 || below >= above
      ? { left, top: r.bottom + gap, maxH: Math.min(256, below) }
      : { left, bottom: vh - r.top + gap, maxH: Math.min(256, above) });
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") { close(); a.focus(); } };
    document.addEventListener("keydown", onKey);
    const raf = requestAnimationFrame(() => {
      const onDown = (e: MouseEvent) => { const t = e.target as Node; if (!popRef.current?.contains(t) && !a.contains(t)) close(); };
      const onScroll = (e: Event) => { if (popRef.current?.contains(e.target as Node)) return; close(); };
      document.addEventListener("mousedown", onDown);
      window.addEventListener("scroll", onScroll, true);
      window.addEventListener("resize", close);
      // Keyboard users land on the first choice.
      (popRef.current?.querySelector("button") as HTMLButtonElement | null)?.focus({ preventScroll: true });
      cleanup.current = () => { document.removeEventListener("mousedown", onDown); window.removeEventListener("scroll", onScroll, true); window.removeEventListener("resize", close); };
    });
    return () => { cancelAnimationFrame(raf); document.removeEventListener("keydown", onKey); cleanup.current?.(); };
  }, [anchorSel, width]);
  if (!pos) return null;
  return createPortal(
    <div ref={popRef} style={{ position: "fixed", left: pos.left, width, zIndex: 70, maxHeight: pos.maxH, ...(pos.top !== undefined ? { top: pos.top } : { bottom: pos.bottom }) }}
      className="bg-white rounded-xl border border-slate-200 shadow-xl p-1.5 overflow-auto text-[11px]">
      {children}
    </div>,
    document.body,
  );
}
