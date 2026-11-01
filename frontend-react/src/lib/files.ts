// Read a File as a data URL (base64) — the shape POST /api/media expects for uploads.
export function readAsDataUrl(f: File): Promise<string> {
  return new Promise((res, rej) => {
    const r = new FileReader();
    r.onload = () => res(String(r.result));
    r.onerror = rej;
    r.readAsDataURL(f);
  });
}

// Phone photos run 3–8 MB; the model takes at most 5 MB per image. Downscale a photo to a long
// edge of `maxEdge` px as JPEG before upload — plenty to read an invoice. PDFs and other
// non-images pass through untouched, as does any photo the browser can't decode or that would
// not get smaller.
export async function shrinkImageFile(f: File, maxEdge = 2000, quality = 0.85): Promise<File> {
  if (!f.type.startsWith("image/") || f.type === "image/gif") return f;
  try {
    const url = URL.createObjectURL(f);
    const img = await new Promise<HTMLImageElement>((res, rej) => {
      const i = new Image();
      i.onload = () => res(i);
      i.onerror = rej;
      i.src = url;
    }).finally(() => URL.revokeObjectURL(url));
    const k = Math.min(1, maxEdge / Math.max(img.width, img.height));
    const c = document.createElement("canvas");
    c.width = Math.round(img.width * k); c.height = Math.round(img.height * k);
    const ctx = c.getContext("2d");
    if (!ctx) return f;
    ctx.drawImage(img, 0, 0, c.width, c.height);
    const blob = await new Promise<Blob | null>((res) => c.toBlob(res, "image/jpeg", quality));
    if (!blob || blob.size >= f.size) return f;
    return new File([blob], f.name.replace(/\.\w+$/, "") + ".jpg", { type: "image/jpeg" });
  } catch {
    return f;
  }
}
