import { describe, it, expect } from "vitest";
import { nextStep } from "./nextStatus";

// The shop's default work-order pipeline.
const S = [
  { key: "new", status_group: "new", sort_order: 0 },
  { key: "diagnosed", status_group: "in_progress", sort_order: 1 },
  { key: "waiting_parts", status_group: "pending", sort_order: 2 },
  { key: "in_repair", status_group: "in_progress", sort_order: 3 },
  { key: "ready", status_group: "ready", sort_order: 4 },
  { key: "done", status_group: "closed", sort_order: 5 },
  { key: "dropped", status_group: "dropped", sort_order: 6 },
];

describe("nextStep (B11: the next-step button)", () => {
  it("goes from Diagnosed to In repair, not to Waiting parts", () => {
    expect(nextStep(S, "diagnosed")?.key).toBe("in_repair");
  });
  it("follows the main path from each status", () => {
    expect(nextStep(S, "new")?.key).toBe("diagnosed");
    expect(nextStep(S, "waiting_parts")?.key).toBe("in_repair");
    expect(nextStep(S, "in_repair")?.key).toBe("ready");
  });
  it("never offers a finishing status as the next step", () => {
    expect(nextStep(S, "ready")).toBeUndefined();
  });
  it("only offers a status this role may move to", () => {
    expect(nextStep(S, "diagnosed", ["waiting_parts", "ready"])?.key).toBe("ready");
  });
  it("offers Waiting parts when it's the only way forward", () => {
    expect(nextStep(S, "diagnosed", ["waiting_parts"])?.key).toBe("waiting_parts");
  });
});
