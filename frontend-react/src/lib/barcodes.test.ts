import { describe, expect, it } from "vitest";
import { gtinCheckDigit, isInStoreEan13, missingBarcodesMessage, sortBarcodes } from "./barcodes";

describe("gtinCheckDigit", () => {
  it("matches the GS1 examples", () => {
    expect(gtinCheckDigit("200000000001")).toBe("5");
    expect(gtinCheckDigit("400638133393")).toBe("1");   // a real EAN-13: 4006381333931
  });
});

describe("isInStoreEan13", () => {
  it("accepts shop-made codes in 20–29 with a valid check digit", () => {
    expect(isInStoreEan13("2000000000015")).toBe(true);
    expect(isInStoreEan13(" 2000000000015 ")).toBe(true);
  });
  it("rejects manufacturer ranges, wrong check digits and other formats", () => {
    expect(isInStoreEan13("4006381333931")).toBe(false);
    expect(isInStoreEan13("2000000000016")).toBe(false);
    expect(isInStoreEan13("RO12345")).toBe(false);
    expect(isInStoreEan13(null)).toBe(false);
  });
});

describe("missingBarcodesMessage", () => {
  it("says plainly what happened", () => {
    expect(missingBarcodesMessage({ generated: 0 }, 0)).toBe("Every product already has a barcode.");
    expect(missingBarcodesMessage({ generated: 0 }, 3)).toBe("Every selected product already has a barcode.");
    expect(missingBarcodesMessage({ generated: 1 }, 0)).toBe("Added a barcode to 1 product. Existing barcodes weren't changed.");
    expect(missingBarcodesMessage({ generated: 5000, more: true }, 0)).toMatch(/Run it again/);
  });
});

describe("sortBarcodes", () => {
  it("puts the main barcode first and keeps the rest in order", () => {
    const rows = [{ id: "a", is_main: false }, { id: "m", is_main: true }, { id: "b", is_main: false }];
    expect(sortBarcodes(rows).map((r) => r.id)).toEqual(["m", "a", "b"]);
  });
});
