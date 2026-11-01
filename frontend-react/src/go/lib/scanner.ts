// Camera barcode/QR scanning for Tekly Go.
//
// Chrome on Android has the native BarcodeDetector. iOS Safari does not, so there we decode
// frames in JS with @zxing/library (pure JS — the CSP has no 'wasm-unsafe-eval', which rules
// out the WASM decoders). The decoder is loaded only when it's needed, so Android and desktop
// never download it.

export type Stop = () => void;

// What shops print: work-order labels (Code 128 / QR), product labels (EAN/UPC/Code 39/128) and
// manufacturer stickers (Data Matrix on some devices).
const NATIVE_FORMATS = ["qr_code", "code_128", "code_39", "ean_13", "ean_8", "upc_a", "upc_e", "data_matrix", "itf"];

type Detector = { detect: (src: CanvasImageSource) => Promise<{ rawValue: string }[]> };

async function nativeDetector(): Promise<Detector | null> {
  const BD = (window as any).BarcodeDetector;
  if (!BD) return null;
  try {
    const supported: string[] = (await BD.getSupportedFormats?.()) ?? NATIVE_FORMATS;
    const formats = NATIVE_FORMATS.filter((f) => supported.includes(f));
    return formats.length ? new BD({ formats }) : null;
  } catch { return null; }
}

type Zxing = typeof import("@zxing/library");

/** A zxing reader for the formats shops print. */
export function makeReader(Z: Zxing) {
  const hints = new Map();
  hints.set(Z.DecodeHintType.POSSIBLE_FORMATS, [
    Z.BarcodeFormat.QR_CODE, Z.BarcodeFormat.CODE_128, Z.BarcodeFormat.CODE_39, Z.BarcodeFormat.EAN_13,
    Z.BarcodeFormat.EAN_8, Z.BarcodeFormat.UPC_A, Z.BarcodeFormat.UPC_E, Z.BarcodeFormat.DATA_MATRIX, Z.BarcodeFormat.ITF,
  ]);
  hints.set(Z.DecodeHintType.TRY_HARDER, true);
  const reader = new Z.MultiFormatReader();
  reader.setHints(hints);
  return reader;
}

/** RGBA pixels → greyscale (the luminance zxing reads). */
export function toLuminance(rgba: Uint8ClampedArray, w: number, h: number): Uint8ClampedArray {
  const lum = new Uint8ClampedArray(w * h);
  for (let i = 0, j = 0; j < lum.length; i += 4, j++) lum[j] = (rgba[i] * 77 + rgba[i + 1] * 150 + rgba[i + 2] * 29) >> 8;
  return lum;
}

/** Decode one greyscale frame; null when no code is in view (most frames). */
export function decodeLuminance(Z: Zxing, reader: InstanceType<Zxing["MultiFormatReader"]>, lum: Uint8ClampedArray, w: number, h: number): string | null {
  try {
    return reader.decodeWithState(new Z.BinaryBitmap(new Z.HybridBinarizer(new Z.RGBLuminanceSource(lum, w, h)))).getText();
  } catch { return null; }
  finally { reader.reset(); }
}

async function zxingDetector(): Promise<Detector> {
  const Z = await import("@zxing/library");
  const reader = makeReader(Z);
  const canvas = document.createElement("canvas");
  const ctx = canvas.getContext("2d", { willReadFrequently: true })!;
  return {
    async detect(src) {
      const v = src as HTMLVideoElement;
      const w = v.videoWidth, h = v.videoHeight;
      if (!w || !h) return [];
      // Decode a downscaled frame: plenty for a label held close, and keeps old iPhones smooth.
      const scale = Math.min(1, 960 / Math.max(w, h));
      canvas.width = Math.round(w * scale); canvas.height = Math.round(h * scale);
      ctx.drawImage(v, 0, 0, canvas.width, canvas.height);
      const { data } = ctx.getImageData(0, 0, canvas.width, canvas.height);
      const text = decodeLuminance(Z, reader, toLuminance(data, canvas.width, canvas.height), canvas.width, canvas.height);
      return text ? [{ rawValue: text }] : [];
    },
  };
}

export function cameraSupported(): boolean {
  return !!navigator.mediaDevices?.getUserMedia;
}

/** Start the back camera on `video` and call `onCode` once with the first code read. */
export async function startScanner(video: HTMLVideoElement, onCode: (code: string) => void): Promise<Stop> {
  const stream = await navigator.mediaDevices.getUserMedia({
    video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 }, height: { ideal: 720 } },
    audio: false,
  });
  let stopped = false;
  let timer: number | undefined;
  const stop: Stop = () => {
    stopped = true;
    if (timer) clearTimeout(timer);
    stream.getTracks().forEach((t) => t.stop());
    video.srcObject = null;
  };
  try {
    video.srcObject = stream;
    video.setAttribute("playsinline", "true");   // iOS: play inline, not full screen
    video.muted = true;
    await video.play();
  } catch (e) { stop(); throw e; }

  const detector = (await nativeDetector()) ?? (await zxingDetector());
  const tick = async () => {
    if (stopped) return;
    try {
      const codes = await detector.detect(video);
      const hit = codes.find((c) => c.rawValue);
      if (hit && !stopped) { stop(); onCode(hit.rawValue); return; }
    } catch { /* a bad frame is not an error */ }
    if (!stopped) timer = window.setTimeout(tick, 120);
  };
  tick();
  return stop;
}

export function haptic(ms = 25) {
  try { navigator.vibrate?.(ms); } catch { /* not supported (iOS PWA) */ }
}
