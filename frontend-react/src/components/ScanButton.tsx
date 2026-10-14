import { useEffect, useRef, useState } from "react";
import { M } from "./Shell";

/**
 * 144s — camera barcode / IMEI scanner. A small glass button that opens a live
 * camera view, decodes a barcode with the browser-native BarcodeDetector, and
 * calls onScan(code). Zero dependencies. Falls back to a clear message where
 * BarcodeDetector isn't available (the field's manual typing still works).
 *
 * ponytail: native BarcodeDetector over a decoder lib. Upgrade path: bundle
 * @zxing/browser only if we must support Safari/Firefox scanning.
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
  const streamRef = useRef<MediaStream | null>(null);
  const rafRef = useRef<number | null>(null);

  const supported = typeof (window as any).BarcodeDetector !== "undefined";

  const stop = () => {
    if (rafRef.current) cancelAnimationFrame(rafRef.current);
    rafRef.current = null;
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
  };

  useEffect(() => {
    if (!open) return;
    if (!supported) { setErr("This browser can't scan — type the code instead."); return; }
    let cancelled = false;
    const detector = new (window as any).BarcodeDetector({
      formats: ["code_128", "code_39", "qr_code", "ean_13", "ean_8", "codabar", "itf", "upc_a", "upc_e"],
    });
    (async () => {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } });
        if (cancelled) { stream.getTracks().forEach((t) => t.stop()); return; }
        streamRef.current = stream;
        const v = videoRef.current;
        if (!v) return;
        v.srcObject = stream;
        await v.play();
        const tick = async () => {
          if (cancelled || !videoRef.current) return;
          try {
            const codes = await detector.detect(videoRef.current);
            if (codes && codes.length) {
              const raw = String(codes[0].rawValue || "").trim();
              if (raw) { onScan(raw); close(); return; }
            }
          } catch { /* transient decode frame error — keep looping */ }
          rafRef.current = requestAnimationFrame(tick);
        };
        rafRef.current = requestAnimationFrame(tick);
      } catch {
        setErr("Camera unavailable — check permissions, or type the code.");
      }
    })();
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
        <div className="fixed inset-0 z-[70] flex items-center justify-center bg-ink-primary/50 backdrop-blur-[2px]" onClick={close}>
          <div className="relative w-[320px] bg-white dark:bg-[#151b2e] rounded-2xl shadow-clay-card border border-slate-200/80 overflow-hidden" onClick={(e) => e.stopPropagation()}>
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
            <div className="px-4 py-2.5 text-[11px] text-ink-tertiary text-center">Point the camera at the barcode or QR code.</div>
          </div>
        </div>
      )}
    </>
  );
}
