import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { shrinkImageFile } from "./files";

beforeEach(() => {
  // jsdom has no object URLs
  (URL as any).createObjectURL = vi.fn(() => "blob:x");
  (URL as any).revokeObjectURL = vi.fn();
});
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("shrinkImageFile", () => {
  it("passes PDFs through untouched", async () => {
    const pdf = new File(["%PDF-1.7"], "inv.pdf", { type: "application/pdf" });
    expect(await shrinkImageFile(pdf)).toBe(pdf);
  });

  it("returns the original photo when the browser can't decode it", async () => {
    class BadImage { onerror: any; onload: any; set src(_: string) { setTimeout(() => this.onerror?.(new Error("x"))); } }
    vi.stubGlobal("Image", BadImage);
    const jpg = new File(["not really a jpeg"], "inv.jpg", { type: "image/jpeg" });
    expect(await shrinkImageFile(jpg)).toBe(jpg);
  });

  it("downscales a large photo to a JPEG no wider than the long edge", async () => {
    class BigImage { width = 4000; height = 3000; onerror: any; onload: any; set src(_: string) { setTimeout(() => this.onload?.()); } }
    vi.stubGlobal("Image", BigImage);
    const drawn: number[] = [];
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({ drawImage: (_: any, __: any, ___: any, w: number, h: number) => drawn.push(w, h) } as any);
    vi.spyOn(HTMLCanvasElement.prototype, "toBlob").mockImplementation(function (cb: BlobCallback) { cb(new Blob(["small"], { type: "image/jpeg" })); });
    const big = new File([new Uint8Array(6 * 1024 * 1024)], "IMG_1234.HEIC.png", { type: "image/png" });
    const out = await shrinkImageFile(big);
    expect(drawn).toEqual([2000, 1500]);
    expect(out.type).toBe("image/jpeg");
    expect(out.name).toBe("IMG_1234.HEIC.jpg");
    expect(out.size).toBeLessThan(big.size);
  });
});
