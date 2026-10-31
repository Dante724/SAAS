import { describe, expect, it, vi } from "vitest";
import { nativeDetector, startCameraScan } from "./cameraScan";
import { BARCODE_TYPES, DETECTOR_FORMATS, barcodeTypeLabel, usableFormats } from "./barcodes";

function fakeVideo() {
  return { srcObject: null as any, play: vi.fn().mockResolvedValue(undefined) } as unknown as HTMLVideoElement;
}
function fakeStream() {
  const stop = vi.fn();
  return { stream: { getTracks: () => [{ stop }] }, stop };
}
function fakeWin(detector?: any) {
  const { stream, stop } = fakeStream();
  const win: any = {
    navigator: { mediaDevices: { getUserMedia: vi.fn().mockResolvedValue(stream) } },
    requestAnimationFrame: (fn: () => void) => { setTimeout(fn, 0); return 1; },
    cancelAnimationFrame: vi.fn(),
  };
  if (detector) win.BarcodeDetector = detector;
  return { win, trackStop: stop };
}

describe("barcode type list", () => {
  it("covers every type the server creates, with plain labels", () => {
    expect(BARCODE_TYPES.map((t) => t.key)).toEqual(
      ["ean13", "ean8", "upca", "upce", "itf14", "itf", "code39", "code128", "gs1_128", "qr", "datamatrix", "pdf417"]);
    expect(barcodeTypeLabel("datamatrix")).toBe("Data Matrix");
    expect(barcodeTypeLabel(null)).toBe("Code 128");
  });
  it("asks the native detector only for formats it says it can read", () => {
    expect(usableFormats(["qr_code", "ean_13", "aztec"])).toEqual(["ean_13", "qr_code"]);
    expect(usableFormats(undefined)).toEqual([]);
    expect(DETECTOR_FORMATS).toContain("data_matrix");
  });
});

describe("camera scanning", () => {
  it("uses the browser's BarcodeDetector when it supports our formats", async () => {
    const detect = vi.fn().mockResolvedValueOnce([]).mockResolvedValueOnce([{ rawValue: "]d2010200000000001521SN1" }]);
    const BD: any = vi.fn().mockImplementation(() => ({ detect }));
    BD.getSupportedFormats = vi.fn().mockResolvedValue(["ean_13", "data_matrix", "qr_code"]);
    const { win, trackStop } = fakeWin(BD);
    const loadZxing = vi.fn();
    const got = await new Promise<string>((resolve) => { startCameraScan(fakeVideo(), resolve, { win, loadZxing }); });
    expect(got).toBe("]d2010200000000001521SN1");
    expect(BD).toHaveBeenCalledWith({ formats: ["ean_13", "qr_code", "data_matrix"] });
    expect(trackStop).toHaveBeenCalled();             // the camera is released after a read
    expect(loadZxing).not.toHaveBeenCalled();
  });

  it("falls back to zxing where there is no detector (Safari, Firefox)", async () => {
    const { win } = fakeWin();
    expect(await nativeDetector(win)).toBeNull();
    const ctrlStop = vi.fn();
    class Reader {
      decodeFromConstraints(_c: any, _v: any, cb: (r: any) => void) {
        setTimeout(() => cb({ getText: () => "0036000291452" }), 0);
        return Promise.resolve({ stop: ctrlStop });
      }
    }
    const loadZxing = vi.fn().mockResolvedValue({ BrowserMultiFormatReader: Reader, hints: new Map() });
    const got = await new Promise<string>((resolve) => { startCameraScan(fakeVideo(), resolve, { win, loadZxing }); });
    expect(got).toBe("0036000291452");
    expect(loadZxing).toHaveBeenCalledOnce();
    expect(ctrlStop).toHaveBeenCalled();
  });

  it("treats a detector with none of our formats as missing", async () => {
    const BD: any = vi.fn();
    BD.getSupportedFormats = vi.fn().mockResolvedValue(["aztec"]);
    const { win } = fakeWin(BD);
    expect(await nativeDetector(win)).toBeNull();
  });
});
