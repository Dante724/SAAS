// The iOS path: no BarcodeDetector, so camera frames are decoded in JS with zxing. Render a real QR
// with zxing's encoder into camera-like frames (a few sizes, slightly tilted, anti-aliased, grey ink
// on off-white) and check the decoder reads them. Perfectly axis-aligned synthetic images are
// avoided on purpose: zxing-js sometimes misses those, and a hand-held camera never produces one.
import { describe, expect, it } from "vitest";
import * as Z from "@zxing/library";
import { decodeLuminance, makeReader, toLuminance } from "./scanner";

function cameraFrame(m: Z.BitMatrix, scale: number, angle: number) {
  const W = Math.round(m.getWidth() * scale) + 40, H = Math.round(m.getHeight() * scale) + 40;
  const rgba = new Uint8ClampedArray(W * H * 4);
  const cx = W / 2, cy = H / 2, ca = Math.cos(angle), sa = Math.sin(angle);
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    let v = 0;
    for (const [ox, oy] of [[0.25, 0.25], [0.75, 0.25], [0.25, 0.75], [0.75, 0.75]]) {   // 4× supersampling
      const px = x + ox - cx, py = y + oy - cy;
      const mx = Math.floor((ca * px + sa * py + cx - 20) / scale), my = Math.floor((-sa * px + ca * py + cy - 20) / scale);
      v += mx >= 0 && my >= 0 && mx < m.getWidth() && my < m.getHeight() && m.get(mx, my) ? 35 : 225;
    }
    const i = (y * W + x) * 4;
    rgba[i] = rgba[i + 1] = rgba[i + 2] = v / 4; rgba[i + 3] = 255;
  }
  return { rgba, W, H };
}

describe("JS barcode decoding (iOS fallback)", () => {
  const reader = makeReader(Z);
  const qr = new Z.QRCodeWriter().encode("208459223724", Z.BarcodeFormat.QR_CODE, 120, 120, new Map());

  it.each([[1.3, 0.05], [2.37, 0.12], [3.3, 0.05], [5.2, 0.12]])("reads a work-order QR at scale %s, tilt %s rad", (scale, angle) => {
    const { rgba, W, H } = cameraFrame(qr, scale, angle);
    expect(decodeLuminance(Z, reader, toLuminance(rgba, W, H), W, H)).toBe("208459223724");
  });

  it("returns null when nothing is in view", () => {
    const w = 64, h = 48;
    const blank = new Uint8ClampedArray(w * h * 4).fill(200);
    expect(decodeLuminance(Z, reader, toLuminance(blank, w, h), w, h)).toBeNull();
  });

  it("converts RGBA to luminance", () => {
    expect(Array.from(toLuminance(new Uint8ClampedArray([255, 255, 255, 255, 0, 0, 0, 255]), 2, 1))).toEqual([255, 0]);
  });
});
