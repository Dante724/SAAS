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
  it("shrinks to 2000px on the long edge (the shared PERF-22 setting), never upscales", () => {
    expect(fitWithin(4032, 3024)).toEqual({ w: 2000, h: 1500 });
    expect(fitWithin(3024, 4032)).toEqual({ w: 1500, h: 2000 });
    expect(fitWithin(800, 600)).toEqual({ w: 800, h: 600 });
  });
});

describe("tasks (due times are UTC instants; days are the shop's)", () => {
  const t = (over: Partial<Task>): Task => ({ id: "t", title: "x", status: "todo", ...over });
  it("UTC shop: groups by when they're due", () => {
    const now = new Date("2026-10-19T10:00:00Z");
    expect(bucket(t({ due_at: "2026-10-19T09:00:00" }), now, "UTC")).toBe("overdue");
    expect(bucket(t({ due_at: "2026-10-19T18:00:00" }), now, "UTC")).toBe("today");
    expect(bucket(t({ due_at: "2026-10-21T18:00:00" }), now, "UTC")).toBe("later");
    expect(bucket(t({}), now, "UTC")).toBe("someday");
    expect(bucket(t({ status: "done", due_at: "2026-10-01T09:00:00" }), now, "UTC")).toBe("done");
    expect(bucket(t({ status: "cancelled", due_at: "2026-10-01T09:00:00" }), now, "UTC")).toBe("cancelled");   // never open or late
    expect(dueCount([t({ status: "cancelled", due_at: "2026-10-19T09:00:00" })], now, "UTC")).toBe(0);
  });
  it("IST shop at 23:30: a task at 00:30 tomorrow (shop) is later, not today; end of today is today", () => {
    const now = new Date("2026-11-12T18:00:00Z");                                           // 23:30 IST
    expect(bucket(t({ due_at: "2026-11-12T18:29:59" }), now, "Asia/Kolkata")).toBe("today");     // 12 Nov end of shop day
    expect(bucket(t({ due_at: "2026-11-12T19:00:00" }), now, "Asia/Kolkata")).toBe("later");     // 13 Nov 00:30 IST
    expect(bucket(t({ due_at: "2026-11-12T19:00:00" }), now, "UTC")).toBe("today");              // the same instant in a UTC shop
    expect(bucket(t({ due_at: "2026-11-12T17:00:00" }), now, "Asia/Kolkata")).toBe("overdue");
  });
  it("badges overdue and due-today tasks only", () => {
    const now = new Date("2026-10-19T10:00:00Z");
    expect(dueCount([t({ due_at: "2026-10-19T09:00:00" }), t({ due_at: "2026-10-19T18:00:00" }), t({}), t({ status: "done", due_at: "2026-10-19T09:00:00" })], now, "UTC")).toBe(2);
  });
  it("quick due dates use the shop's date; a picked time carries the shop's offset", () => {
    const now = new Date("2026-11-12T19:00:00Z");                                           // 00:30 on 13 Nov in India
    expect(dueFor("today", "", now, "Asia/Kolkata")).toBe("2026-11-13");
    expect(dueFor("tomorrow", "", now, "Asia/Kolkata")).toBe("2026-11-14");
    expect(dueFor("today", "", now, "UTC")).toBe("2026-11-12");
    expect(dueFor("none", "", now, "UTC")).toBeNull();
    expect(dueFor("pick", "2026-11-20T09:30", now, "Asia/Kolkata")).toBe("2026-11-20T09:30:00+05:30");
    expect(dueFor("pick", "2026-11-20T09:30", now, "UTC")).toBe("2026-11-20T09:30:00+00:00");
  });
});

import { smsLink, smsSegments, waLink } from "./messaging";

describe("messaging links", () => {
  it("builds SMS links the way each phone wants them", () => {
    expect(smsLink("+44 7700 900123", "Ready & paid?", true)).toBe("sms:+447700900123&body=Ready%20%26%20paid%3F");
    expect(smsLink("+447700900123", "Hi", false)).toBe("sms:+447700900123?body=Hi");
  });
  it("builds wa.me links without the plus", () => {
    expect(waLink("+447700900123", "Hi Sara")).toBe("https://wa.me/447700900123?text=Hi%20Sara");
  });
  it("counts SMS segments", () => {
    expect(smsSegments("")).toBe(0);
    expect(smsSegments("a".repeat(160))).toBe(1);
    expect(smsSegments("a".repeat(161))).toBe(2);
    expect(smsSegments("Ready 👍")).toBe(1);
    expect(smsSegments("👍".repeat(40))).toBe(2);
  });
});
