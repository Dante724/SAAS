// Vitest setup — adds jest-dom matchers (toBeInTheDocument, etc.) and cleans
// up the DOM between tests.
import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

afterEach(() => cleanup());

// Node 25+ ships its own global `localStorage`, which is empty and method-less unless Node runs
// with --localstorage-file, and it shadows jsdom's. Give tests a working Storage either way
// (CI pins Node 22, where jsdom's is already in place).
if (typeof globalThis.localStorage?.clear !== "function") {
  const data = new Map<string, string>();
  const store: Storage = {
    get length() { return data.size; },
    clear: () => data.clear(),
    getItem: (k) => (data.has(k) ? data.get(k)! : null),
    key: (i) => [...data.keys()][i] ?? null,
    removeItem: (k) => { data.delete(k); },
    setItem: (k, v) => { data.set(k, String(v)); },
  };
  Object.defineProperty(globalThis, "localStorage", { value: store, configurable: true, writable: true });
}
