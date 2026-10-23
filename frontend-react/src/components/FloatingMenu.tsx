import { useLayoutEffect, useRef, useState } from "react";
import type React from "react";
import { createPortal } from "react-dom";

// The visible area a menu should stay inside: the nearest scrolling box around the
// button (e.g. a drawer's body, so the menu doesn't cover its sticky footer),
// clipped to the window. Falls back to the window.
function boundsFor(a: HTMLElement) {
  const vh = window.innerHeight, edge = 8;
  for (let p = a.parentElement; p && p !== document.body; p = p.parentElement) {
    const oy = getComputedStyle(p).overflowY;
    if ((oy === "auto" || oy === "scroll") && p.scrollHeight > p.clientHeight) {
      const b = p.getBoundingClientRect();
      return { top: Math.max(edge, b.top), bottom: Math.min(vh - edge, b.bottom) };
    }
  }
  return { top: edge, bottom: vh - edge };
}

// A small menu drawn above the page (portal + fixed position), so cards with
// overflow-hidden can't clip it. Opens below its button, or above when there
// isn't room; follows the button when the page scrolls and closes once the
// button scrolls out of sight. Closes on an outside click or Escape; arrow keys
// move between choices.
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
    const place = (onScroll = false) => {
      const r = a.getBoundingClientRect();
      const W = width, gap = 4, vh = window.innerHeight;
      let { top: bTop, bottom: bBot } = boundsFor(a);
      if (onScroll && (r.bottom < bTop || r.top > bBot)) { close(); return; }   // scrolled out of view
      const want = Math.min(320, popRef.current?.scrollHeight || 256);
      // If the scrolling box is too short for the menu either way, use the whole window.
      if (Math.max(bBot - r.bottom, r.top - bTop) - gap < Math.min(want, 160)) { bTop = 8; bBot = vh - 8; }
      const left = Math.max(8, Math.min(r.left, window.innerWidth - W - 8));
      const below = bBot - r.bottom - gap, above = r.top - gap - bTop;
      setPos(below >= want || below >= above
        ? { left, top: r.bottom + gap, maxH: Math.min(want, below) }
        : { left, bottom: vh - r.top + gap, maxH: Math.min(want, above) });
    };
    place();
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") { e.stopPropagation(); close(); a.focus(); } };
    document.addEventListener("keydown", onKey);
    let frame = 0;
    const raf = requestAnimationFrame(() => {
      place();   // re-measure now the menu has its real height
      const onDown = (e: MouseEvent) => { const t = e.target as Node; if (!popRef.current?.contains(t) && !a.contains(t)) close(); };
      const onScroll = (e: Event) => {
        if (e.target instanceof Node && popRef.current?.contains(e.target)) return;   // scrolling the menu itself
        cancelAnimationFrame(frame); frame = requestAnimationFrame(() => place(true));
      };
      document.addEventListener("mousedown", onDown);
      window.addEventListener("scroll", onScroll, true);
      window.addEventListener("resize", onScroll);
      // Content that grows after opening (a list that loads, "show all") re-places the menu.
      const ro = typeof ResizeObserver !== "undefined" && popRef.current?.firstElementChild ? new ResizeObserver(() => place()) : null;
      if (ro && popRef.current) Array.from(popRef.current.children).forEach((c) => ro.observe(c));
      // Keyboard users land on the first choice.
      (popRef.current?.querySelector("button") as HTMLButtonElement | null)?.focus({ preventScroll: true });
      cleanup.current = () => { document.removeEventListener("mousedown", onDown); window.removeEventListener("scroll", onScroll, true); window.removeEventListener("resize", onScroll); ro?.disconnect(); cancelAnimationFrame(frame); };
    });
    return () => { cancelAnimationFrame(raf); document.removeEventListener("keydown", onKey); cleanup.current?.(); };
  }, [anchorSel, width]);
  const onKeyDown = (e: React.KeyboardEvent) => {
    if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key)) return;
    const items = Array.from(popRef.current?.querySelectorAll<HTMLButtonElement>("button:not([disabled])") || []);
    if (!items.length) return;
    e.preventDefault();
    const i = items.indexOf(document.activeElement as HTMLButtonElement);
    const next = e.key === "Home" ? 0 : e.key === "End" ? items.length - 1
      : e.key === "ArrowDown" ? (i + 1) % items.length : (i - 1 + items.length) % items.length;
    items[next].focus();
  };
  return createPortal(
    <div ref={popRef} onKeyDown={onKeyDown}
      style={{ position: "fixed", width, zIndex: 70, ...(pos ? { left: pos.left, maxHeight: pos.maxH, ...(pos.top !== undefined ? { top: pos.top } : { bottom: pos.bottom }) } : { left: 0, top: 0, visibility: "hidden" }) }}
      className="bg-white rounded-xl border border-slate-200 shadow-xl p-1.5 overflow-auto overscroll-contain text-[11px]">
      {children}
    </div>,
    document.body,
  );
}
