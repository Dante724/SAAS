// Vitest setup — adds jest-dom matchers (toBeInTheDocument, etc.) and cleans
// up the DOM between tests.
import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

afterEach(() => cleanup());

// Node 25+ ships its own global `localStorage`, which shadows jsdom's and is unusable without
// --localstorage-file (methods are missing), so tests that touch storage broke on newer Node.
// CI runs Node 22 and never sees this; give newer Node an in-memory Storage with the same API.
if (typeof globalThis.localStorage?.clear !== "function") {
  class MemoryStorage implements Storage {
    private m = new Map<string, string>();
    get length() { return this.m.size; }
    clear() { this.m.clear(); }
    getItem(k: string) { return this.m.has(k) ? this.m.get(k)! : null; }
    key(i: number) { return [...this.m.keys()][i] ?? null; }
    removeItem(k: string) { this.m.delete(k); }
    setItem(k: string, v: string) { this.m.set(k, String(v)); }
  }
  for (const name of ["localStorage", "sessionStorage"] as const) {
    Object.defineProperty(globalThis, name, { value: new MemoryStorage(), configurable: true, writable: true });
  }
}
