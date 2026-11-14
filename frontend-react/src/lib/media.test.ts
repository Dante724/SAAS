import { describe, expect, it } from "vitest";
import { FileTooLargeError, MEDIA_MAX_BYTES, MEDIA_MAX_MB, TOO_LARGE_MESSAGE, dataUrlBytes, dataUrlMime, fileForUpload, fitWithin } from "./media";

const b64 = (n: number) => btoa("x".repeat(n));

describe("shared media limits (PERF-22)", () => {
  it("is 10 MB with the server's wording", () => {
    expect(MEDIA_MAX_MB).toBe(10);
    expect(MEDIA_MAX_BYTES).toBe(10 * 1024 * 1024);
    expect(TOO_LARGE_MESSAGE).toBe("That file is over the 10 MB limit. Try a smaller file.");
    expect(new FileTooLargeError().message).toBe(TOO_LARGE_MESSAGE);
  });

  it("works out a data URL's size without decoding it", () => {
    expect(dataUrlBytes(`data:application/pdf;base64,${b64(3000)}`)).toBe(3000);
    expect(dataUrlBytes(`data:application/pdf;base64,${b64(3001)}`)).toBe(3001);
    expect(dataUrlBytes("data:text/plain,hello")).toBe(5);
    expect(dataUrlMime("data:image/webp;base64,AAAA", "image/png")).toBe("image/webp");
    expect(dataUrlMime("garbage", "image/png")).toBe("image/png");
  });

  it("fits photos inside 2000 px on the long side and never upscales", () => {
    expect(fitWithin(4032, 3024)).toEqual({ w: 2000, h: 1500 });
    expect(fitWithin(3024, 4032)).toEqual({ w: 1500, h: 2000 });
    expect(fitWithin(800, 600)).toEqual({ w: 800, h: 600 });
  });

  it("refuses a file over the limit before uploading it", async () => {
    const big = new File([new Uint8Array(MEDIA_MAX_BYTES + 1)], "scan.pdf", { type: "application/pdf" });
    await expect(fileForUpload(big)).rejects.toThrow(TOO_LARGE_MESSAGE);
  });

  it("passes a file under the limit through as a data URL (PDFs untouched)", async () => {
    const pdf = new File([new TextEncoder().encode("%PDF-1.4 hello")], "a.pdf", { type: "application/pdf" });
    const d = await fileForUpload(pdf);
    expect(d.startsWith("data:application/pdf;base64,")).toBe(true);
    expect(atob(d.split(",")[1])).toBe("%PDF-1.4 hello");
  });
});
