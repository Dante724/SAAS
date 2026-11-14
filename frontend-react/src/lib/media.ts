// PERF-22 (owner decision 4): one limit per file and one photo shrink, the same in the web app,
// Tekly Go and the public check-in page. The server enforces the same numbers (backend/config.py
// MEDIA_MAX_BYTES, MEDIA_IMAGE_MAX_EDGE, MEDIA_IMAGE_QUALITY; a backend test reads MEDIA_MAX_MB from
// this file) and shrinks again as a safety net. There is no total storage cap for a shop.
import { readAsDataUrl } from "./files";

export const MEDIA_MAX_MB = 10;
export const MEDIA_MAX_BYTES = MEDIA_MAX_MB * 1024 * 1024;
export const PHOTO_MAX_EDGE = 2000;
export const PHOTO_QUALITY = 0.8;

/** Same words as the server's 413 (backend/services/media.py too_large_message). */
export const TOO_LARGE_MESSAGE = `That file is over the ${MEDIA_MAX_MB} MB limit. Try a smaller file.`;

export class FileTooLargeError extends Error {
  constructor() { super(TOO_LARGE_MESSAGE); this.name = "FileTooLargeError"; }
}

/** Decoded size of a data URL, without decoding it. */
export function dataUrlBytes(d: string): number {
  const i = d.indexOf(",");
  const body = i >= 0 ? d.slice(i + 1) : d;
  if (i >= 0 && !d.slice(0, i).includes(";base64")) return body.length;
  const pad = body.endsWith("==") ? 2 : body.endsWith("=") ? 1 : 0;
  return Math.max(0, Math.floor((body.length * 3) / 4) - pad);
}

/** Size that fits inside `max` on the long edge, keeping the aspect ratio (never upscales). */
export function fitWithin(w: number, h: number, max = PHOTO_MAX_EDGE): { w: number; h: number } {
  if (!w || !h) return { w: 0, h: 0 };
  const s = Math.min(1, max / Math.max(w, h));
  return { w: Math.round(w * s), h: Math.round(h * s) };
}

// Stills a browser can redraw. GIF (animation), SVG (vector) and HEIC (most browsers can't decode
// it) go up as they are; the server's safety net handles what it can.
const SHRINKABLE = new Set(["image/jpeg", "image/jpg", "image/pjpeg", "image/png", "image/webp"]);

function loadImage(file: Blob): Promise<HTMLImageElement> {
  return new Promise((res, rej) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => { URL.revokeObjectURL(url); res(img); };
    img.onerror = () => { URL.revokeObjectURL(url); rej(new Error("That file isn't a photo we can read.")); };
    img.src = url;
  });
}

/** A photo redrawn at most PHOTO_MAX_EDGE on the long side, as a data URL. Redrawing on a canvas
 *  drops EXIF (camera, GPS); browsers apply the EXIF orientation when decoding, so it stays
 *  upright. A PNG keeps transparency as WebP where the browser can encode it. Null when the
 *  browser can't decode or encode it. */
export async function shrinkPhotoFile(f: Blob, quality = PHOTO_QUALITY): Promise<string | null> {
  if (!SHRINKABLE.has((f.type || "").toLowerCase())) return null;
  try {
    const img = await loadImage(f);
    const { w, h } = fitWithin(img.naturalWidth, img.naturalHeight);
    if (!w || !h) return null;
    const c = document.createElement("canvas");
    c.width = w; c.height = h;
    const ctx = c.getContext("2d");
    if (!ctx) return null;
    ctx.drawImage(img, 0, 0, w, h);
    const type = f.type === "image/png" ? "image/webp" : "image/jpeg";
    const out = c.toDataURL(type, quality);
    // A browser that can't encode WebP hands back PNG: keep that only if it isn't bigger.
    if (!out.startsWith("data:image/")) return null;
    return out;
  } catch {
    return null;
  }
}

/** The data URL to upload for a picked file: a photo is shrunk first (when the browser can), and
 *  anything still over the limit is refused here with the same message the server would give. */
export async function fileForUpload(f: File): Promise<string> {
  const shrunk = await shrinkPhotoFile(f);
  if (shrunk && dataUrlBytes(shrunk) <= Math.max(f.size, 1)) return checked(shrunk);
  if (f.size > MEDIA_MAX_BYTES) throw new FileTooLargeError();
  return checked(await readAsDataUrl(f));
}

function checked(d: string): string {
  if (dataUrlBytes(d) > MEDIA_MAX_BYTES) throw new FileTooLargeError();
  return d;
}

/** The mime type a data URL carries (after a shrink it may differ from the picked file's). */
export function dataUrlMime(d: string, fallback = ""): string {
  const m = /^data:([^;,]+)/.exec(d);
  return m ? m[1] : fallback;
}
