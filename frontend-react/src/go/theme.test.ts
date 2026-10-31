import { beforeEach, describe, expect, it, vi } from "vitest";
import { applyTheme, getTheme, setTheme } from "./theme";

// the phone says dark: Go must still open light unless the person picked Dark in More
beforeEach(() => {
  localStorage.clear();
  vi.stubGlobal("matchMedia", (q: string) => ({ matches: q.includes("dark"), addEventListener: () => {}, removeEventListener: () => {} }));
});

describe("Tekly Go theme", () => {
  it("is light by default, even on a dark phone", () => {
    expect(getTheme()).toBe("light");
    applyTheme();
    expect(document.documentElement.classList.contains("dark")).toBe(false);
  });

  it("goes dark only when Dark is picked", () => {
    setTheme("dark");
    expect(getTheme()).toBe("dark");
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    setTheme("light");
    expect(getTheme()).toBe("light");
    expect(document.documentElement.classList.contains("dark")).toBe(false);
  });

  it("follows the phone only when asked to", () => {
    setTheme("system");
    expect(getTheme()).toBe("system");
    expect(document.documentElement.classList.contains("dark")).toBe(true);
  });
});
