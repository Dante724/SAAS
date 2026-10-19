// Job photos from the phone: shrink on the device (long edge 1600px, JPEG ~0.72), which also drops
// EXIF/GPS because the image is redrawn on a canvas; upload to /api/media; then append the ids to
// the job with POST /orders/{id}/intake-photos/add (never the replace-everything PATCH).
import { api } from "../../api/client";

export const MAX_EDGE = 1600;

/** Size that fits inside `max` on the long edge, keeping the aspect ratio (never upscales). */
export function fitWithin(w: number, h: number, max = MAX_EDGE): { w: number; h: number } {
  if (!w || !h) return { w: 0, h: 0 };
  const s = Math.min(1, max / Math.max(w, h));
  return { w: Math.round(w * s), h: Math.round(h * s) };
}

function loadImage(file: Blob): Promise<HTMLImageElement> {
  return new Promise((res, rej) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => { URL.revokeObjectURL(url); res(img); };
    img.onerror = () => { URL.revokeObjectURL(url); rej(new Error("That file isn't a photo we can read.")); };
    img.src = url;
  });
}

/** A camera photo → a small JPEG data URL with no EXIF. */
export async function shrinkPhoto(file: Blob, quality = 0.72): Promise<string> {
  const img = await loadImage(file);
  const { w, h } = fitWithin(img.naturalWidth, img.naturalHeight);
  const c = document.createElement("canvas");
  c.width = w; c.height = h;
  c.getContext("2d")!.drawImage(img, 0, 0, w, h);
  return c.toDataURL("image/jpeg", quality);
}

export async function uploadPhoto(dataUrl: string, name = "photo.jpg"): Promise<string> {
  const r = await api.post<{ id: string }>("/media", { data: dataUrl, name, mime: "image/jpeg" });
  return r.id;
}

export type IntakePhoto = { id: string; url: string; marked_url?: string | null };

export async function addPhotosToJob(orderId: string, ids: string[]): Promise<IntakePhoto[]> {
  const r = await api.post<{ intake_photos: IntakePhoto[] }>(`/orders/${encodeURIComponent(orderId)}/intake-photos/add`, { photos: ids });
  return r.intake_photos;
}

export function newKey(): string {
  try { return crypto.randomUUID(); } catch { return `k${Date.now()}${Math.random().toString(36).slice(2)}`; }
}
