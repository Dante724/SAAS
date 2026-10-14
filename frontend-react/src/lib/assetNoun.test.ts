// Vitest: assetNoun — which noun one record shows. A record stamped with a REAL
// template keeps its noun (history keeps its wording); unstamped/"general"/default
// records follow the org's current noun.
import { describe, it, expect } from "vitest";
import { assetNoun, type Terminology } from "./useTerminology";

const fallback: Terminology = {
  asset: "Vehicle", assets: "Vehicles", woButton: "+ Order", key: "auto",
  groups: [], identifier: { primary: "vin", allowed: ["vin"] },
};

describe("assetNoun", () => {
  it("null/undefined record → fallback noun", () => {
    expect(assetNoun(null, fallback)).toBe("Vehicle");
    expect(assetNoun(undefined, fallback)).toBe("Vehicle");
  });

  it("record stamped with a real template keeps its own noun", () => {
    expect(assetNoun({ asset_label: "Vessel", template_key: "marine" }, fallback)).toBe("Vessel");
  });

  it("'general' template is the never-chosen default → follows current org noun", () => {
    expect(assetNoun({ asset_label: "Asset", template_key: "general" }, fallback)).toBe("Vehicle");
  });

  it("missing label or template_key → fallback", () => {
    expect(assetNoun({ asset_label: null, template_key: "marine" }, fallback)).toBe("Vehicle");
    expect(assetNoun({ asset_label: "Vessel", template_key: null }, fallback)).toBe("Vehicle");
  });

  it("the literal default label 'Asset' never overrides the current noun", () => {
    expect(assetNoun({ asset_label: "Asset", template_key: "auto" }, fallback)).toBe("Vehicle");
  });
});
