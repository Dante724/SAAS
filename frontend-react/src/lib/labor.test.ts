import { describe, it, expect, afterEach } from "vitest";
import { setLaborSpelling, laborText, laborWord } from "./labor";

describe("Labor / Labour follows the shop's country", () => {
  afterEach(() => setLaborSpelling(null, null));

  it("US shops read Labor", () => {
    setLaborSpelling("US", "USD");
    expect(laborWord()).toBe("Labor");
    expect(laborText("Services and labour")).toBe("Services and labor");
    expect(laborText("Labour cost")).toBe("Labor cost");
  });
  it("everyone else reads Labour", () => {
    setLaborSpelling("IN", "INR");
    expect(laborText("Labor price")).toBe("Labour price");
    expect(laborWord(true)).toBe("labour");
  });
  it("without a country, a US-dollar shop counts as US", () => {
    setLaborSpelling(undefined, "USD");
    expect(laborWord()).toBe("Labor");
    setLaborSpelling(undefined, "GBP");
    expect(laborWord()).toBe("Labour");
  });
  it("leaves other words alone", () => {
    setLaborSpelling("GB", "GBP");
    expect(laborText("laboratory and labor_performed")).toBe("laboratory and labor_performed");
  });
});
