import { describe, expect, it } from "vitest";
import { mergeBuckets, sortQueue } from "./queue";
import { duePresets, missingFields, orderBody, quoteTotal, type IntakeField } from "./newJob";

describe("my queue", () => {
  const j = (id: string, extra: object = {}) => ({ id, ...extra });

  it("puts overdue first, then priority, then the earliest promise, then the earliest booking", () => {
    const jobs = [
      j("plain"),
      j("booked-late", { scheduled_for: "2030-01-02T10:00:00" }),
      j("booked-early", { scheduled_for: "2030-01-01T10:00:00" }),
      j("due-soon", { due_at: "2030-01-01T09:00:00" }),
      j("urgent", { priority: 1 }),
      j("vip", { priority: 2 }),
      j("late", { due_at: "2020-01-01T09:00:00" }),
    ];
    expect(sortQueue(jobs, new Set(["late"])).map((x) => x.id))
      .toEqual(["late", "vip", "urgent", "due-soon", "booked-early", "booked-late", "plain"]);
  });

  it("keeps the server's order for ties and doesn't mutate the input", () => {
    const jobs = [j("a"), j("b"), j("c")];
    expect(sortQueue(jobs).map((x) => x.id)).toEqual(["a", "b", "c"]);
    expect(jobs.map((x) => x.id)).toEqual(["a", "b", "c"]);
  });

  it("merges dashboard buckets without duplicates", () => {
    expect(mergeBuckets([j("a"), j("b")], [j("b"), j("c")]).map((x) => x.id)).toEqual(["a", "b", "c"]);
  });
});

describe("tablet intake", () => {
  const base = { isEstimate: false, complaint: "Screen" };

  it("sends priority, promised-by date, type, filled custom fields and quote lines", () => {
    const body = orderBody({
      ...base, priority: 1, dueAt: "2030-05-01T17:00", typeId: "t1",
      customData: { passcode: "1234", accessories: ["Charger"], empty: "", none: [] },
      lines: [
        { kind: "service", ref_id: "s1", description: "Screen fit", qty: 1, unit_price: 40 },
        { kind: "product", ref_id: "p1", description: "Screen", qty: 1, unit_price: 80 },
        { kind: "custom", description: "Quoted repair", qty: 1, unit_price: 10 },
      ],
    });
    expect(body).toMatchObject({ priority: 1, type_id: "t1", custom_data: { passcode: "1234", accessories: ["Charger"] } });
    expect(body.custom_data).not.toHaveProperty("empty");
    expect(body.due_at).toBe("2030-05-01T17:00:00");   // shop-local wall clock, like scheduled times
    expect(body.lines).toEqual([
      { description: "Screen fit", qty: 1, unit_price: 40, kind: "service", line_type: "labour", ref_id: "s1" },
      { description: "Screen", qty: 1, unit_price: 80, kind: "product", line_type: "part", ref_id: "p1" },
      { description: "Quoted repair", qty: 1, unit_price: 10, kind: "custom", line_type: "labour" },
    ]);
  });

  it("leaves the extras out when they're not set, and sends condition only with a new device", () => {
    const body = orderBody({ ...base, clientId: "c1", deviceId: "d1", condition: "Scratched" });
    expect(Object.keys(body).sort()).toEqual(["assigned_to_id", "client_id", "complaint", "device_id", "is_estimate"]);
    const nd = orderBody({ ...base, clientId: "c1", newDevice: { brand: "Apple", model: "", serial: "", imei: "" }, condition: " Scratched " });
    expect(nd.asset_condition).toBe("Scratched");
  });

  it("offers today only while there's still time", () => {
    expect(duePresets(new Date(2030, 0, 1, 9, 0)).map((p) => p.label)).toEqual(["Today 5pm", "Tomorrow", "In 3 days", "In a week"]);
    const late = duePresets(new Date(2030, 0, 1, 18, 0));
    expect(late[0]).toEqual({ label: "Tomorrow", value: "2030-01-02T17:00" });
  });

  it("names required fields still empty, ignoring yes/no ones", () => {
    const f = (key: string, field_type = "text", required = true): IntakeField => ({ key, label: key.toUpperCase(), field_type, options: [], required });
    const fields = [f("passcode"), f("accessories", "multiselect"), f("water", "bool"), f("notes", "text", false)];
    expect(missingFields(fields, { accessories: [] })).toEqual(["PASSCODE", "ACCESSORIES"]);
    expect(missingFields(fields, { passcode: "0000", accessories: ["Case"] })).toEqual([]);
  });

  it("adds up the quote", () => {
    expect(quoteTotal([{ kind: "custom", description: "x", qty: 2, unit_price: 15 }, { kind: "service", description: "y", qty: 1, unit_price: 5 }])).toBe(35);
  });
});
