import { describe, it, expect } from "vitest";
import { NAV, CATS, SEC_CAT, SEC_KW, searchSections } from "./settingsRegistry";

const catIds = new Set(CATS.map((c) => c.id));

describe("settings registry integrity", () => {
  it("has no duplicate section ids", () => {
    const ids = NAV.map((n) => n.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  // The regression guard: a section missing a category silently vanishes from the
  // grouped nav. Every section MUST be categorized into a real category.
  it("every section is in a real category", () => {
    for (const n of NAV) {
      expect(SEC_CAT[n.id], `${n.id} has no category`).toBeDefined();
      expect(catIds.has(SEC_CAT[n.id]), `${n.id} → unknown category ${SEC_CAT[n.id]}`).toBe(true);
    }
  });

  it("every section has search keywords", () => {
    for (const n of NAV) {
      expect(SEC_KW[n.id], `${n.id} has no keywords`).toBeTruthy();
    }
  });

  it("every category contains at least one section (no empty groups)", () => {
    for (const c of CATS) {
      expect(NAV.some((n) => SEC_CAT[n.id] === c.id), `category ${c.id} is empty`).toBe(true);
    }
  });
});

describe("searchSections", () => {
  it("returns nothing for an empty query", () => {
    expect(searchSections("")).toEqual([]);
    expect(searchSections("   ")).toEqual([]);
  });

  it.each([
    ["invoice number", "finance"],
    ["reminder", "notifications"],
    ["add tech", "team"],
    ["tax", "pricing"],
    ["vin", "industry"],
    ["warehouse", "warehouses"],
    ["sso", "sso"],
  ])("search %j surfaces the %s section", (q, expectedId) => {
    const hits = searchSections(q).map((h) => h.id);
    expect(hits).toContain(expectedId);
  });

  it("is case-insensitive and matches labels too", () => {
    expect(searchSections("APPROVALS").map((h) => h.id)).toContain("approvals");
  });

  it("returns [] for gibberish", () => {
    expect(searchSections("zzzznotathing")).toEqual([]);
  });
});

describe("integrations that aren't real yet stay hidden", () => {
  it("E-commerce isn't in the nav or search without the beta flag", () => {
    expect(NAV.some((n) => n.id === "ecommerce")).toBe(false);
    expect(searchSections("shopify")).toEqual([]);
  });
});
