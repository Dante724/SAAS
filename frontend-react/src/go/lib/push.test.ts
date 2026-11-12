import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { goPath, pushTag } from "./push";

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
  it("opens task alerts on Go's Tasks screen, not the desktop page", () => {
    expect(goPath("/tasks?task=t-9")).toBe("/go/tasks?task=t-9");
    expect(goPath("https://shop.tekly.app/tasks?task=t-9")).toBe("/go/tasks?task=t-9");
    expect(goPath("/tasks")).toBe("/go/tasks");
  });
  it("tags task alerts per task, everything else by kind", () => {
    expect(pushTag("task.overdue", "/tasks?task=t-1")).toBe("task.overdue:t-1");
    expect(pushTag("task.overdue", "/tasks?task=t-2")).toBe("task.overdue:t-2");
    expect(pushTag("order.ready", "/orders?open=o1")).toBe("order.ready");
    expect(pushTag(null, null)).toBe("tekly-go");
  });
  it("the service worker's copies give the same answers", () => {
    const src = readFileSync(join(__dirname, "../../../public/go/sw.js"), "utf8");
    const fn = (name: string) => new Function(`${src.match(new RegExp(`function ${name}\\([^]*?\\n}`))![0]}; return ${name};`)();
    const swGoPath = fn("goPath"), swTag = fn("pushTag");
    for (const l of ["/tasks?task=t-9", "/tasks", "/orders?open=o9", "/tech#/order/a", "/tech", "/inventory", "/inquiries?lead=l1", ""]) {
      expect(swGoPath(l)).toBe(goPath(l));
      expect(swTag("task.overdue", l)).toBe(pushTag("task.overdue", l));
    }
  });
});
