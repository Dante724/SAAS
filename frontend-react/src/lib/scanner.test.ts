import { describe, it, expect, vi } from "vitest";
import { createWedgeDetector } from "./scanner";

function feed(det: ReturnType<typeof createWedgeDetector>, text: string, start: number, gap: number) {
  let t = start;
  for (const ch of text) { det.key(ch, t); t += gap; }
  return det.key("Enter", t);
}

describe("keyboard-wedge detector", () => {
  it("fires on a fast burst that ends in Enter", () => {
    const onScan = vi.fn();
    const det = createWedgeDetector(onScan);
    expect(feed(det, "0123456789012", 1000, 8)).toBe(true);
    expect(onScan).toHaveBeenCalledWith("0123456789012");
  });

  it("ignores a person typing slowly", () => {
    const onScan = vi.fn();
    const det = createWedgeDetector(onScan);
    expect(feed(det, "ABC123", 1000, 180)).toBe(false);
    expect(onScan).not.toHaveBeenCalled();
  });

  it("ignores codes shorter than the minimum and a stray Enter", () => {
    const onScan = vi.fn();
    const det = createWedgeDetector(onScan, { minLength: 4 });
    expect(feed(det, "12", 1000, 5)).toBe(false);
    expect(det.key("Enter", 5000)).toBe(false);
    expect(onScan).not.toHaveBeenCalled();
  });

  it("starts a new burst after a pause, so earlier typing doesn't leak into the code", () => {
    const onScan = vi.fn();
    const det = createWedgeDetector(onScan);
    det.key("x", 0); det.key("y", 20);
    expect(feed(det, "SKU-77", 2000, 6)).toBe(true);
    expect(onScan).toHaveBeenCalledWith("SKU-77");
  });

  it("a slow Enter after a fast burst is not a scan", () => {
    const onScan = vi.fn();
    const det = createWedgeDetector(onScan);
    "SKU-77".split("").forEach((c, i) => det.key(c, i * 5));
    expect(det.key("Enter", 900)).toBe(false);
    expect(onScan).not.toHaveBeenCalled();
  });
});
