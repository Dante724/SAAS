import { useEffect, useRef, useState } from "react";
import { M } from "./Shell";
import { startCameraScan } from "../lib/cameraScan";

/**
 * 144s — camera barcode / IMEI scanner. A small glass button that opens a live
 * camera view and calls onScan(code) with the first code read.
 *
 * ii144b: every type the shop prints or receives (EAN/UPC, ITF, Code 39/128, GS1-128, QR,
 * Data Matrix, PDF417) — the browser's BarcodeDetector where it has one, otherwise
 * @zxing/browser loaded on demand (Safari, Firefox). See lib/cameraScan.ts. A USB or
 * Bluetooth scanner typing into the field keeps working everywhere.
 */
export default function ScanButton({
  onScan,
  title = "Scan barcode",
  className = "",
}: {
  onScan: (code: string) => void;
  title?: string;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const stopRef = useRef<(() => void) | null>(null);

  const stop = () => { stopRef.current?.(); stopRef.current = null; };

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    const v = videoRef.current;
    if (!v) return;
    if (!navigator.mediaDevices?.getUserMedia) { setErr("This browser can't use the camera — type the code instead."); return; }
    startCameraScan(v, (code) => { if (!cancelled) { onScan(code.trim()); close(); } })
      .then((s) => { if (cancelled) s(); else stopRef.current = s; })
      .catch(() => { if (!cancelled) setErr("Camera unavailable — check permissions, or type the code."); });
    return () => { cancelled = true; stop(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const close = () => { stop(); setOpen(false); setErr(null); };

  return (
    <>
      <button
        type="button"
        title={title}
        aria-label={title}
        onClick={() => setOpen(true)}
        className={`shrink-0 w-9 h-9 grid place-items-center rounded-lg glass-input text-primary hover:bg-blue-50 ${className}`}
      >
        <M n="qr_code_scanner" c="text-base" />
      </button>

      {open && (
        <div className="fixed inset-0 z-[70] flex items-center justify-center bg-ink-primary/50 backdrop-blur-[2px]" role="presentation" onClick={close}>
          <div className="relative w-[320px] bg-raised rounded-2xl shadow-clay-card border border-slate-200/80 overflow-hidden" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between px-4 py-3 border-b border-slate-100">
              <span className="text-sm font-bold text-ink-primary">{title}</span>
              <button aria-label="Close" type="button" onClick={close} className="w-7 h-7 rounded-lg hover:bg-slate-100 grid place-items-center text-ink-tertiary"><M n="close" c="text-base" /></button>
            </div>
            <div className="relative bg-ink-primary/90 aspect-[4/3] grid place-items-center">
              {err ? (
                <div className="text-center px-6 text-xs text-white/90">
                  <M n="videocam_off" c="text-2xl block mb-2 opacity-80" />
                  {err}
                </div>
              ) : (
                <>
                  <video ref={videoRef} muted playsInline className="w-full h-full object-cover" />
                  <div className="pointer-events-none absolute inset-8 border-2 border-white/80 rounded-lg" />
                </>
              )}
            </div>
            <div className="px-4 py-2.5 text-[11px] text-ink-tertiary text-center">Point the camera at any barcode, QR or Data Matrix code.</div>
          </div>
        </div>
      )}
    </>
  );
}
