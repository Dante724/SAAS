import { describe, it, expect } from "vitest";
import { buildPattern, parsePattern, renderNumber } from "./FinanceNumbering";

// The live example must match what the server issues (services/numbering.render).
describe("document number patterns", () => {
  const d = new Date(2026, 9, 5);
  it("renders counters, padding and dates like the server", () => {
    expect(renderNumber("INV-{C:5}", 42, d)).toBe("INV-00042");
    expect(renderNumber("WO-{Y}-{C:4}", 7, d)).toBe("WO-2026-0007");
    expect(renderNumber("{D}{M}-{C}", 12, d)).toBe("0510-12");
    expect(renderNumber("S-{C:3}", 12345, d)).toBe("S-12345");
  });
  it("splits simple patterns for the friendly editor and rebuilds them unchanged", () => {
    for (const t of ["INV-{C:5}", "WO-{Y}-{C:4}", "WO-{Y}/{C:4}", "WO{Y}{C:4}", "{C}", "PO{C:6}"]) {
      const p = parsePattern(t);
      expect(p).not.toBeNull();
      expect(buildPattern(p!)).toBe(t);
    }
  });
  it("leaves unusual patterns to the custom editor", () => {
    expect(parsePattern("INV-{M}{Y}-{C:4}")).toBeNull();
    expect(parsePattern("{C:4}-X")).toBeNull();
  });
});
