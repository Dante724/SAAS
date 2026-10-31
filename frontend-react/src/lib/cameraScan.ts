// ii144b — read a barcode from the camera on phones and tablets without a USB scanner.
//
// Uses the browser's own BarcodeDetector where it exists and supports the formats (Chrome on
// Android, recent Edge); everywhere else (Safari, Firefox, desktop Chrome without the API) it loads
// @zxing/browser on demand, so the decoder only costs bandwidth for people who open the camera.
// Both read the same formats the server creates: EAN-13/8, UPC-A/E, ITF, Code 39/128 (incl. GS1-128),
// QR, Data Matrix and PDF417. The raw text goes to the server, which strips scanner prefixes and
// reads GS1 data (product number, batch, expiry, serial).
import { usableFormats } from "./barcodes";

export type StopFn = () => void;

type Detector = { detect: (src: CanvasImageSource) => Promise<{ rawValue?: string }[]> };

/** The native detector, or null when the browser has none (or none of our formats). */
export async function nativeDetector(win: any = window): Promise<Detector | null> {
  const BD = win?.BarcodeDetector;
  if (!BD) return null;
  let formats: string[] = [];
  try { formats = usableFormats(await BD.getSupportedFormats?.()); } catch { formats = []; }
  if (!formats.length) return null;
  try { return new BD({ formats }) as Detector; } catch { return null; }
}

/** Start the camera on `video` and call `onCode` once with the first code read. Returns a stop
 *  function that releases the camera; it is safe to call more than once. */
export async function startCameraScan(video: HTMLVideoElement, onCode: (code: string) => void,
  deps: { win?: any; loadZxing?: () => Promise<any> } = {}): Promise<StopFn> {
  const win = deps.win ?? window;
  let stopped = false;
  const detector = await nativeDetector(win);
  if (detector) {
    const stream: MediaStream = await win.navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } });
    video.srcObject = stream;
    await video.play();
    let raf = 0;
    const stop = () => { stopped = true; if (raf) win.cancelAnimationFrame(raf); stream.getTracks().forEach((t) => t.stop()); };
    const tick = async () => {
      if (stopped) return;
      try {
        const found = await detector.detect(video);
        const raw = String(found?.[0]?.rawValue || "");
        if (raw.trim()) { stop(); onCode(raw); return; }
      } catch { /* a frame that couldn't be read — keep going */ }
      raf = win.requestAnimationFrame(tick);
    };
    raf = win.requestAnimationFrame(tick);
    return stop;
  }
  const zx = await (deps.loadZxing ?? loadZxing)();
  const reader = new zx.BrowserMultiFormatReader(zx.hints);
  let controls: { stop: () => void } | null = null;
  const stop = () => { stopped = true; controls?.stop(); };
  controls = await reader.decodeFromConstraints({ video: { facingMode: "environment" } }, video,
    (result: any) => {
      if (stopped || !result) return;
      const raw = String(result.getText?.() ?? result.text ?? "");
      if (raw.trim()) { stop(); onCode(raw); }
    });
  if (stopped) controls?.stop();
  return stop;
}

async function loadZxing() {
  const [{ BrowserMultiFormatReader }, lib] = await Promise.all([import("@zxing/browser"), import("@zxing/library")]);
  const F = lib.BarcodeFormat;
  const hints = new Map();
  hints.set(lib.DecodeHintType.POSSIBLE_FORMATS, [F.EAN_13, F.EAN_8, F.UPC_A, F.UPC_E, F.ITF, F.CODE_39, F.CODE_128,
    F.QR_CODE, F.DATA_MATRIX, F.PDF_417, F.CODABAR]);
  hints.set(lib.DecodeHintType.TRY_HARDER, true);
  return { BrowserMultiFormatReader, hints };
}
