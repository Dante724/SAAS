import { describe, expect, it, vi } from "vitest";
import { GO_CLIENT, noteLabelOpen } from "./client";

describe("Go client tag", () => {
  it("names itself go/<version>", () => {
    expect(GO_CLIENT).toMatch(/^go\/[A-Za-z0-9._-]+$/);
  });
});

describe("noteLabelOpen", () => {
  it("reports a job opened from a label and drops the marker from the address", async () => {
    const report = vi.fn().mockResolvedValue({});
    const replace = vi.fn();
    expect(noteLabelOpen("https://shop.tekly.app/go/job/abc?via=qr", report, replace)).toBe(true);
    expect(replace).toHaveBeenCalledWith("/go/job/abc");
    expect(report).toHaveBeenCalledWith("https://shop.tekly.app/go/job/abc");
  });
  it("keeps other query parameters", () => {
    const replace = vi.fn();
    noteLabelOpen("https://s/go/job/abc?tab=photos&via=qr#top", vi.fn().mockResolvedValue({}), replace);
    expect(replace).toHaveBeenCalledWith("/go/job/abc?tab=photos#top");
  });
  it("ignores ordinary opens and other screens", () => {
    const report = vi.fn().mockResolvedValue({});
    expect(noteLabelOpen("https://s/go/job/abc", report, vi.fn())).toBe(false);
    expect(noteLabelOpen("https://s/go/today?via=qr", report, vi.fn())).toBe(false);
    expect(noteLabelOpen("not a url", report, vi.fn())).toBe(false);
    expect(report).not.toHaveBeenCalled();
  });
  it("a failed report never throws", async () => {
    expect(noteLabelOpen("https://s/go/job/x?via=qr", vi.fn().mockRejectedValue(new Error("offline")), vi.fn())).toBe(true);
    await Promise.resolve();
  });
});
