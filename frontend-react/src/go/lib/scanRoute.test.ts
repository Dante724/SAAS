import { describe, expect, it, vi } from "vitest";
import { directTarget, normalizeCode, routeScan, targetFor } from "./scanRoute";

describe("normalizeCode", () => {
  it("strips wedge-scanner line endings and padding", () => {
    expect(normalizeCode("  WO-00012\r\n")).toBe("WO-00012");
    expect(normalizeCode("ABC\t")).toBe("ABC");
    expect(normalizeCode("")).toBe("");
  });
});

describe("directTarget", () => {
  it("opens Tekly Go and web-app job links without a lookup", () => {
    expect(directTarget("https://shop.tekly.app/go/job/abc-123")).toEqual({ kind: "job", path: "/job/abc-123" });
    expect(directTarget("/go/part/p1")).toEqual({ kind: "part", path: "/part/p1" });
    expect(directTarget("https://shop.tekly.app/orders?open=o-9")).toEqual({ kind: "job", path: "/job/o-9" });
  });
  it("leaves plain codes and other links to the server", () => {
    expect(directTarget("WO-00012")).toBeNull();
    expect(directTarget("8901234567890")).toBeNull();
    expect(directTarget("https://shop.tekly.app/track/AbCdEf123456")).toBeNull();
    expect(directTarget("https://shop.tekly.app/orders")).toBeNull();
  });
});

describe("targetFor", () => {
  it("routes each resolve type to its screen", () => {
    expect(targetFor({ matched: true, type: "order", id: "o1", label: "WO-1" }, "x")).toEqual({ kind: "job", path: "/job/o1", label: "WO-1" });
    expect(targetFor({ matched: true, type: "product", id: "p1", label: "Screen" }, "x")).toEqual({ kind: "part", path: "/part/p1", label: "Screen" });
    expect(targetFor({ matched: true, type: "asset", id: "d1", label: "iPhone 14" }, "x")).toEqual({ kind: "asset", path: "/asset/d1", label: "iPhone 14" });
  });
  it("encodes ids and reports a miss with the code", () => {
    expect(targetFor({ matched: true, type: "order", id: "a/b" }, "x")).toMatchObject({ path: "/job/a%2Fb" });
    expect(targetFor({ matched: false, code: "NOPE" }, "NOPE")).toEqual({ kind: "none", code: "NOPE" });
  });
});

describe("routeScan", () => {
  it("sends the cleaned code to resolve and routes the answer", async () => {
    const resolve = vi.fn().mockResolvedValue({ matched: true, type: "order", id: "o7", label: "WO-7" });
    expect(await routeScan(" wo-00007\n", resolve)).toEqual({ kind: "job", path: "/job/o7", label: "WO-7" });
    expect(resolve).toHaveBeenCalledWith("wo-00007");
  });
  it("passes tracking-link QRs to resolve (the server knows the token)", async () => {
    const resolve = vi.fn().mockResolvedValue({ matched: true, type: "order", id: "o8" });
    await routeScan("https://shop/track/AbCdEf123456", resolve);
    expect(resolve).toHaveBeenCalledWith("https://shop/track/AbCdEf123456");
  });
  it("skips the network for direct links and empty input", async () => {
    const resolve = vi.fn();
    expect(await routeScan("https://x/go/job/j1", resolve)).toEqual({ kind: "job", path: "/job/j1" });
    expect(await routeScan("   ", resolve)).toEqual({ kind: "none", code: "" });
    expect(resolve).not.toHaveBeenCalled();
  });
  it("reports an unknown code", async () => {
    expect(await routeScan("ZZZ", async () => ({ matched: false, code: "ZZZ" }))).toEqual({ kind: "none", code: "ZZZ" });
  });
});
