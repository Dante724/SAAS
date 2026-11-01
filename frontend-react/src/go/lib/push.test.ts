import { describe, expect, it } from "vitest";
import { goPath } from "./push";

describe("goPath (where a notification opens)", () => {
  it("opens jobs in Tekly Go", () => {
    expect(goPath("/tech#/order/abc-1")).toBe("/go/job/abc-1");
    expect(goPath("https://shop.tekly.app/tech#/order/abc-1")).toBe("/go/job/abc-1");
    expect(goPath("/orders?open=o9")).toBe("/go/job/o9");
    expect(goPath("/orders?tab=x&open=o9")).toBe("/go/job/o9");
  });
  it("sends the technician home and empty links to Today", () => {
    expect(goPath("/tech")).toBe("/go/today");
    expect(goPath("/tech#/dashboard")).toBe("/go/today");
    expect(goPath(null)).toBe("/go/today");
  });
  it("leaves other web-app pages alone", () => {
    expect(goPath("/inventory")).toBe("/inventory");
  });
});
