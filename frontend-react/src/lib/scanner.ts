// F1 — barcode scanning for the POS.
//
// A USB/Bluetooth "keyboard wedge" scanner types the code as very fast keystrokes and finishes
// with Enter. createWedgeDetector() tells that apart from a person typing: every key must follow
// the previous one within maxGapMs, and the burst must be at least minLength characters.
// useWedgeScanner() listens on the whole page but ignores keys aimed at a text field, so typing a
// client's name never adds a product (a field that wants scans handles its own Enter).
import { useEffect, useRef } from "react";

export type WedgeOptions = { minLength?: number; maxGapMs?: number };

export function createWedgeDetector(onScan: (code: string) => void, { minLength = 4, maxGapMs = 50 }: WedgeOptions = {}) {
  let buf = "";
  let last = 0;
  return {
    /** Feed one key; returns true when it completed a scan (so the caller can swallow the Enter). */
    key(key: string, at: number): boolean {
      const gap = at - last;
      last = at;
      if (key === "Enter") {
        const code = buf;
        buf = "";
        if (code.length >= minLength && gap <= maxGapMs) { onScan(code); return true; }
        return false;
      }
      if (key.length !== 1) { buf = ""; return false; }        // Shift, arrows, F-keys…
      buf = gap <= maxGapMs ? buf + key : key;                   // a slow key starts a new burst
      return false;
    },
    reset() { buf = ""; last = 0; },
  };
}

function isEditable(t: EventTarget | null): boolean {
  const el = t as HTMLElement | null;
  if (!el || !el.tagName) return false;
  const tag = el.tagName.toLowerCase();
  return tag === "input" || tag === "textarea" || tag === "select" || el.isContentEditable;
}

export function useWedgeScanner(onScan: (code: string) => void, opts: WedgeOptions & { enabled?: boolean } = {}) {
  const cb = useRef(onScan);
  cb.current = onScan;
  const { enabled = true, minLength, maxGapMs } = opts;
  useEffect(() => {
    if (!enabled) return;
    const det = createWedgeDetector((c) => cb.current(c), { minLength, maxGapMs });
    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      if (isEditable(e.target)) { det.reset(); return; }
      if (det.key(e.key, e.timeStamp || performance.now())) e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [enabled, minLength, maxGapMs]);
}

/** A short confirmation tone: high for "added", low for "no match". Silent where audio is blocked. */
export function scanBeep(ok: boolean) {
  try {
    const Ctx = (window as any).AudioContext || (window as any).webkitAudioContext;
    if (!Ctx) return;
    const ctx: AudioContext = new Ctx();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = "square";
    osc.frequency.value = ok ? 1320 : 220;
    gain.gain.value = 0.05;
    osc.connect(gain).connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + (ok ? 0.08 : 0.25));
    osc.onended = () => { ctx.close().catch(() => {}); };
  } catch { /* no audio: the visual flash still shows */ }
}
