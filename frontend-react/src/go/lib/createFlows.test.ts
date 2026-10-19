import { describe, expect, it } from "vitest";
import { addProblem, orderBody, splitNamePhone } from "./newJob";
import { changeDue, clampAmount, finishTarget, money, quickTender } from "./pay";
import { fitWithin } from "./photos";
import { bucket, dueCount, type Task } from "../screens/Tasks";
import { dueFor } from "../components/TaskSheet";

describe("new job", () => {
  it("splits 'Maria 0412 345 678' into name and phone", () => {
    expect(splitNamePhone("Maria 0412 345 678")).toEqual({ name: "Maria", phone: "0412 345 678" });
    expect(splitNamePhone("+44 7700 900123 Sara Khan")).toEqual({ name: "Sara Khan", phone: "+44 7700 900123" });
    expect(splitNamePhone("Tom Reed")).toEqual({ name: "Tom Reed", phone: "" });
    expect(splitNamePhone("Unit 12")).toEqual({ name: "Unit 12", phone: "" });   // too few digits for a phone
  });

  it("builds the order body: existing device, inline new device, walk-in", () => {
    const base = { isEstimate: false, complaint: "  Cracked screen ", assignedToId: "e1" };
    expect(orderBody({ ...base, clientId: "c1", deviceId: "d1" })).toEqual(
      { complaint: "Cracked screen", is_estimate: false, client_id: "c1", assigned_to_id: "e1", device_id: "d1" });
    expect(orderBody({ ...base, clientId: "c1", newDevice: { brand: "Apple", model: "iPhone 14", serial: "", imei: "35 1234 567890 12" } })).toMatchObject(
      { asset_brand: "Apple", asset_model: "iPhone 14", asset_imei: "35123456789012" });
    const walkIn = orderBody({ ...base, isEstimate: true, complaint: "", newDevice: { brand: "X", model: "", serial: "", imei: "" } });
    expect(walkIn).toEqual({ complaint: null, is_estimate: true, client_id: null, assigned_to_id: "e1" });   // no owner → no device
  });

  it("problem chips add to the text", () => {
    expect(addProblem("", "Battery")).toBe("Battery");
    expect(addProblem("Cracked screen.", "Battery")).toBe("Cracked screen, battery");
    expect(addProblem("Battery drains", "Battery")).toBe("Battery drains");
  });
});

describe("payment", () => {
  it("offers exact and the next round notes", () => {
    expect(quickTender(37.5)).toEqual([37.5, 40, 50, 100]);
    expect(quickTender(20)).toEqual([20, 50, 100, 500]);
    expect(quickTender(4000)).toEqual([4000, 5000, 10000, 20000]);   // round amounts still get options
    expect(quickTender(0)).toEqual([]);
  });
  it("works out change and clamps part payments", () => {
    expect(changeDue(50, 37.5)).toBe(12.5);
    expect(changeDue(20, 37.5)).toBe(0);
    expect(clampAmount("40", 100)).toBe(40);
    expect(clampAmount("150", 100)).toBe(100);     // never above what's owed
    expect(clampAmount("0", 100)).toBeNull();
    expect(clampAmount("abc", 100)).toBeNull();
  });
  it("shows cents", () => {
    expect(money("£", 49.99)).toBe(`£${(49.99).toLocaleString(undefined, { minimumFractionDigits: 2 })}`);
  });
  it("finishes to the first allowed closed status", () => {
    const st = [
      { key: "ready", name: "Ready", status_group: "ready", sort_order: 1 },
      { key: "cancel", name: "Cancelled", status_group: "dropped", is_terminal: true, sort_order: 2 },
      { key: "done", name: "Done", status_group: "closed", is_terminal: true, sort_order: 3 },
      { key: "delivered", name: "Delivered", status_group: "closed", is_terminal: true, sort_order: 4 },
    ];
    expect(finishTarget(st, null)?.key).toBe("done");
    expect(finishTarget(st, ["ready", "delivered"])?.key).toBe("delivered");
    expect(finishTarget(st, ["ready", "cancel"])).toBeNull();     // cancelling isn't finishing
  });
});

describe("photos", () => {
  it("shrinks to 1600px on the long edge, never upscales", () => {
    expect(fitWithin(4032, 3024)).toEqual({ w: 1600, h: 1200 });
    expect(fitWithin(3024, 4032)).toEqual({ w: 1200, h: 1600 });
    expect(fitWithin(800, 600)).toEqual({ w: 800, h: 600 });
  });
});

describe("tasks", () => {
  const now = new Date(2026, 9, 19, 10, 0);
  const t = (over: Partial<Task>): Task => ({ id: "t", title: "x", status: "todo", ...over });
  it("groups by when they're due", () => {
    expect(bucket(t({ due_at: "2026-10-19T09:00" }), now)).toBe("overdue");
    expect(bucket(t({ due_at: "2026-10-19T18:00" }), now)).toBe("today");
    expect(bucket(t({ due_at: "2026-10-21T18:00" }), now)).toBe("later");
    expect(bucket(t({}), now)).toBe("someday");
    expect(bucket(t({ status: "done", due_at: "2026-10-01T09:00" }), now)).toBe("done");
  });
  it("badges overdue and due-today tasks only", () => {
    expect(dueCount([t({ due_at: "2026-10-19T09:00" }), t({ due_at: "2026-10-19T18:00" }), t({}), t({ status: "done", due_at: "2026-10-19T09:00" })], now)).toBe(2);
  });
  it("quick due dates land at 18:00", () => {
    expect(dueFor("today", "", now)).toBe("2026-10-19T18:00");
    expect(dueFor("tomorrow", "", now)).toBe("2026-10-20T18:00");
    expect(dueFor("none", "", now)).toBeNull();
    expect(dueFor("pick", "2026-11-01T09:30", now)).toBe("2026-11-01T09:30");
  });
});
