// Vitest: asset status tone (the fix was "not everything amber").
import { describe, it, expect } from "vitest";
import { assetStatusTone } from "./statusTone";

describe("assetStatusTone", () => {
  it("normal states stay quiet", () => {
    expect(assetStatusTone("in use").quiet).toBe(true);
    expect(assetStatusTone("with_customer").quiet).toBe(true);
    expect(assetStatusTone("in_repair").quiet).toBe(true);
    expect(assetStatusTone(undefined).quiet).toBe(true);
    expect(assetStatusTone(null).label).toBe("In Use");
  });

  it("exceptions get flagged with the right colour", () => {
    expect(assetStatusTone("awaiting_parts").quiet).toBe(false);
    expect(assetStatusTone("awaiting_parts").cls).toContain("amber");
    expect(assetStatusTone("overdue").cls).toContain("amber");
    expect(assetStatusTone("scrapped").cls).toContain("rose");
    expect(assetStatusTone("out_of_service").cls).toContain("rose");
    expect(assetStatusTone("ready_for_pickup").cls).toContain("emerald");
  });

  it("humanises underscores in the label", () => {
    expect(assetStatusTone("awaiting_parts").label).toBe("Awaiting Parts");
  });
});
