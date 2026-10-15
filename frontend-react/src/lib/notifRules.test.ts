// Vitest: the status editor and Settings › Notifications read customer messages from the same rules.
import { describe, it, expect, vi } from "vitest";
import { cellRules, channelPhrase, firesOnStatus, firingLabel, statusEvent, toggleCell, type NotifRule } from "./notifRules";

const rule = (p: Partial<NotifRule>): NotifRule => ({ id: Math.random().toString(36).slice(2), module: "order", scope: "client",
  event: "status_changed", channel: "email", trigger_status_key: null, record_type: null, active: true, ...p });

// A seeded shop: email on created / every status change / closed.
const seeded = [rule({ event: "created" }), rule({ event: "status_changed" }), rule({ event: "closed" })];

describe("statusEvent", () => {
  it("finishing stages close the job", () => {
    expect(statusEvent({ status_group: "closed" })).toBe("closed");
    expect(statusEvent({ status_group: "dropped" })).toBe("closed");
    expect(statusEvent({ status_group: "ready" })).toBe("status_changed");
    expect(statusEvent({ is_terminal: false, status_group: "closed" })).toBe("status_changed");
  });
});

describe("firesOnStatus — the editor's read-out", () => {
  it("a seeded shop emails via the every-status rule", () => {
    const f = firesOnStatus(seeded, "work_order", "status_changed", "ready");
    expect(f.map((x) => firingLabel(x, "work_order"))).toEqual(["Email · via Every status change"]);
    const done = firesOnStatus(seeded, "work_order", "closed", "done");
    expect(done.map((x) => firingLabel(x, "work_order"))).toEqual(["Email · via Job finished"]);
  });
  it("a status's own rule reads as just the channel; others' rules, off rules and other record types don't fire", () => {
    const rules = [rule({ channel: "sms", trigger_status_key: "ready" }), rule({ channel: "sms", trigger_status_key: "other" }),
      rule({ event: "status_changed", active: false }), rule({ record_type: "estimate" })];
    const f = firesOnStatus(rules, "work_order", "status_changed", "ready");
    expect(f.map((x) => firingLabel(x, "work_order"))).toEqual(["Text"]);
    expect(f[0].own).toBe(true);
  });
  it("an unsaved status only gets the every-status rules", () => {
    const rules = [...seeded, rule({ channel: "sms", trigger_status_key: "ready" })];
    expect(firesOnStatus(rules, "work_order", "status_changed", undefined).map((x) => x.channel)).toEqual(["email"]);
  });
  it("flags location-scoped rules", () => {
    const [f] = firesOnStatus([rule({ trigger_status_key: "ready", location_id: "L1" })], "work_order", "status_changed", "ready");
    expect(firingLabel(f, "work_order")).toBe("Email · one location");
  });
});

describe("toggleCell — same switch logic as the Notifications table", () => {
  const fakeApi = () => ({ post: vi.fn(async () => ({})), patch: vi.fn(async () => ({})) });
  it("creates the per-status rule when there is none", async () => {
    const api = fakeApi();
    expect(await toggleCell(api, seeded, "work_order", "status_changed", "ready", "sms")).toBe(true);
    expect(api.post).toHaveBeenCalledWith("/notifications/rules", { module: "order", scope: "client", record_type: "work_order",
      event: "status_changed", trigger_status_key: "ready", channel: "sms", active: true });
  });
  it("turns an active rule off, and re-enables an off one instead of duplicating it", async () => {
    const on = rule({ id: "r1", channel: "sms", trigger_status_key: "ready" });
    const api = fakeApi();
    expect(await toggleCell(api, [on], "work_order", "status_changed", "ready", "sms")).toBe(false);
    expect(api.patch).toHaveBeenCalledWith("/notifications/rules/r1", { active: false });
    const api2 = fakeApi();
    await toggleCell(api2, [{ ...on, active: false }], "work_order", "status_changed", "ready", "sms");
    expect(api2.patch).toHaveBeenCalledWith("/notifications/rules/r1", { active: true });
    expect(api2.post).not.toHaveBeenCalled();
  });
  it("cellRules never mixes a status's rule with the every-status one", () => {
    const own = rule({ trigger_status_key: "ready" });
    expect(cellRules([...seeded, own], "work_order", "status_changed", "ready", "email")).toEqual([own]);
  });
});

describe("channelPhrase", () => {
  it("reads naturally", () => {
    expect(channelPhrase(["email"])).toBe("email");
    expect(channelPhrase(["sms", "email", "email"])).toBe("text and email");
    expect(channelPhrase(["email", "sms", "whatsapp"])).toBe("email, text and WhatsApp");
  });
});
