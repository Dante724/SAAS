// Vitest: identifier fields are template-driven (backend owns the rule); this
// maps allowed kinds → renderable Device-column inputs, in template order.
import { describe, it, expect } from "vitest";
import { identifierFields } from "./assetIdentifiers";

describe("identifierFields", () => {
  it("no template → falls back to a single Serial field (every asset needs one id)", () => {
    const f = identifierFields();
    expect(f.map((x) => x.kind)).toEqual(["serial"]);
    expect(f[0].label).toBe("Serial number");
  });

  it("empty allowed list → Serial fallback", () => {
    expect(identifierFields([]).map((x) => x.kind)).toEqual(["serial"]);
  });

  it("renders the allowed renderable kinds in template order", () => {
    // Phone Repair: imei + serial
    expect(identifierFields(["imei", "serial"]).map((x) => x.kind)).toEqual(["imei", "serial"]);
  });

  it("drops kinds with no Device column (barcode, asset_tag, engine_serial)", () => {
    const f = identifierFields(["vin", "barcode", "engine_serial", "asset_tag"]);
    expect(f.map((x) => x.kind)).toEqual(["vin"]);
  });

  it("all-unrenderable allowed list → Serial fallback (never empty)", () => {
    expect(identifierFields(["barcode", "asset_tag"]).map((x) => x.kind)).toEqual(["serial"]);
  });

  it("primary is pulled to the front when it is one of the allowed", () => {
    // Marine: hin+vin+engine_serial, primary hin → hin first, engine_serial dropped
    expect(identifierFields(["vin", "hin"], "hin").map((x) => x.kind)).toEqual(["hin", "vin"]);
  });

  it("primary that is not renderable/allowed is ignored (order unchanged)", () => {
    expect(identifierFields(["imei", "serial"], "vin").map((x) => x.kind)).toEqual(["imei", "serial"]);
  });

  it("each field carries a label and placeholder", () => {
    const f = identifierFields(["vin"]);
    expect(f[0]).toMatchObject({ kind: "vin", label: "VIN" });
    expect(f[0].placeholder).toBeTruthy();
  });
});
