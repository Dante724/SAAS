// Job photos from the phone: shrink on the device with the shared settings (lib/media.ts, PERF-22:
// long edge 2000px, JPEG 0.8, the 10 MB per-file limit), which also drops EXIF/GPS because the
// image is redrawn on a canvas; upload to /api/media; then append the ids to the job with
// POST /orders/{id}/intake-photos/add (never the replace-everything PATCH).
import { api } from "../../api/client";
import { FileTooLargeError, MEDIA_MAX_BYTES, PHOTO_MAX_EDGE, PHOTO_QUALITY, dataUrlBytes, fitWithin } from "../../lib/media";

export { fitWithin };
export const MAX_EDGE = PHOTO_MAX_EDGE;

function loadImage(file: Blob): Promise<HTMLImageElement> {
  return new Promise((res, rej) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => { URL.revokeObjectURL(url); res(img); };
    img.onerror = () => { URL.revokeObjectURL(url); rej(new Error("That file isn't a photo we can read.")); };
    img.src = url;
  });
}

/** A camera photo → a JPEG data URL with no EXIF, within the per-file limit. */
export async function shrinkPhoto(file: Blob, quality = PHOTO_QUALITY): Promise<string> {
  const img = await loadImage(file);
  const { w, h } = fitWithin(img.naturalWidth, img.naturalHeight);
  const c = document.createElement("canvas");
  c.width = w; c.height = h;
  c.getContext("2d")!.drawImage(img, 0, 0, w, h);
  const out = c.toDataURL("image/jpeg", quality);
  if (dataUrlBytes(out) > MEDIA_MAX_BYTES) throw new FileTooLargeError();
  return out;
}

export async function uploadPhoto(dataUrl: string, name = "photo.jpg"): Promise<string> {
  const r = await api.post<{ id: string }>("/media", { data: dataUrl, name, mime: "image/jpeg" });
  return r.id;
}

/** What a job photo shows. Unlabelled photos are just "photos". */
export type PhotoLabel = "intake" | "before" | "after" | "damage";
export const PHOTO_LABELS: { key: PhotoLabel; name: string }[] = [
  { key: "intake", name: "Check-in" }, { key: "before", name: "Before" },
  { key: "after", name: "After" }, { key: "damage", name: "Damage" },
];
export const labelName = (l?: PhotoLabel | null) => PHOTO_LABELS.find((x) => x.key === l)?.name ?? "";

export type IntakePhoto = { id: string; url: string; marked_url?: string | null; label?: PhotoLabel | null };

export async function addPhotosToJob(orderId: string, ids: string[], label?: PhotoLabel | null): Promise<IntakePhoto[]> {
  const r = await api.post<{ intake_photos: IntakePhoto[] }>(`/orders/${encodeURIComponent(orderId)}/intake-photos/add`, { photos: ids, ...(label ? { label } : {}) });
  return r.intake_photos;
}

export async function setPhotoLabel(orderId: string, photoId: string, label: PhotoLabel | null): Promise<IntakePhoto[]> {
  const r = await api.patch<{ intake_photos: IntakePhoto[] }>(`/orders/${encodeURIComponent(orderId)}/intake-photos/${encodeURIComponent(photoId)}/label`, { label });
  return r.intake_photos;
}

export function newKey(): string {
  try { return crypto.randomUUID(); } catch { return `k${Date.now()}${Math.random().toString(36).slice(2)}`; }
}
