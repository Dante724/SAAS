import { describe, expect, it } from "vitest";
import { toAppPath } from "./deepLink";

describe("toAppPath", () => {
  it("sends technician-app links to the same place in the main app", () => {
    expect(toAppPath("/tech#/order/abc-123")).toBe("/orders?open=abc-123");
    expect(toAppPath("https://shop.example/tech#/order/x")).toBe("/orders?open=x");
    expect(toAppPath("/tech#/dashboard")).toBe("/my-work");
    expect(toAppPath("/tech")).toBe("/my-work");
  });
  it("leaves main-app links alone", () => {
    expect(toAppPath("/orders?open=1")).toBe("/orders?open=1");
    expect(toAppPath("inventory")).toBe("/inventory");
    expect(toAppPath(null)).toBe("/");
  });
});
