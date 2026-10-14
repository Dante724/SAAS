// Vitest: cross-device prefs. localStorage is the instant-paint cache; the server
// is the source of truth. Here we pin the cache behaviour and the write-through,
// with the api client mocked so no network happens.
import { describe, it, expect, beforeEach, vi } from "vitest";

const get = vi.fn();
const put = vi.fn();
vi.mock("../api/client", () => ({ api: { get: (...a: any[]) => get(...a), put: (...a: any[]) => put(...a) } }));

import { cachedPref, savePref, loadPref } from "./userPref";

beforeEach(() => {
  localStorage.clear();
  get.mockReset();
  put.mockReset();
  put.mockResolvedValue(undefined);
});

describe("cachedPref", () => {
  it("returns the fallback when nothing is cached", () => {
    expect(cachedPref("listview:x", { density: "cozy" })).toEqual({ density: "cozy" });
  });

  it("returns the parsed cached value when present", () => {
    localStorage.setItem("listview:x", JSON.stringify({ density: "compact" }));
    expect(cachedPref("listview:x", { density: "cozy" })).toEqual({ density: "compact" });
  });

  it("falls back on corrupt JSON instead of throwing", () => {
    localStorage.setItem("listview:x", "{not json");
    expect(cachedPref("listview:x", { density: "cozy" })).toEqual({ density: "cozy" });
  });
});

describe("savePref (write-through)", () => {
  it("updates the cache immediately and pushes to the server", () => {
    savePref("listview:x", { density: "compact" });
    expect(JSON.parse(localStorage.getItem("listview:x")!)).toEqual({ density: "compact" });
    expect(put).toHaveBeenCalledWith("/prefs/listview%3Ax", { value: { density: "compact" } });
  });

  it("a server write failure does not throw", () => {
    put.mockRejectedValue(new Error("offline"));
    expect(() => savePref("listview:x", { a: 1 })).not.toThrow();
  });
});

describe("loadPref (reconcile)", () => {
  it("prefers a non-empty server value and refreshes the cache", async () => {
    get.mockResolvedValue({ value: { density: "compact" } });
    const v = await loadPref("listview:x", { density: "cozy" });
    expect(v).toEqual({ density: "compact" });
    expect(JSON.parse(localStorage.getItem("listview:x")!)).toEqual({ density: "compact" });
  });

  it("empty server value → falls back to cache/fallback", async () => {
    get.mockResolvedValue({ value: {} });
    localStorage.setItem("listview:x", JSON.stringify({ density: "compact" }));
    expect(await loadPref("listview:x", { density: "cozy" })).toEqual({ density: "compact" });
  });

  it("server error (offline/logged-out) → falls back to cache", async () => {
    get.mockRejectedValue(new Error("401"));
    localStorage.setItem("listview:x", JSON.stringify({ density: "compact" }));
    expect(await loadPref("listview:x", { density: "cozy" })).toEqual({ density: "compact" });
  });

  it("server error with no cache → fallback", async () => {
    get.mockRejectedValue(new Error("401"));
    expect(await loadPref("listview:x", { density: "cozy" })).toEqual({ density: "cozy" });
  });
});
