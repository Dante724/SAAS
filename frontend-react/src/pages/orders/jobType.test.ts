import { describe, it, expect } from "vitest";
import { jobTypeLabel } from "./jobType";

const types = [{ id: "t1", name: "Repair" }, { id: "t2", name: "Warranty" }];

describe("jobTypeLabel", () => {
  it("shows the job's own type, so a warranty job reads as warranty", () => {
    expect(jobTypeLabel({ type_id: "t2" }, types)).toBe("Warranty");
    expect(jobTypeLabel({ type_id: "t1" }, types)).toBe("Repair");
  });
  it("never falls back to a made-up type", () => {
    expect(jobTypeLabel({ type_id: null }, types)).toBe("No type set");
    expect(jobTypeLabel({ type_id: "gone" }, types)).toBe("Type no longer in use");
    expect(jobTypeLabel({ type_id: "t1" }, null)).toBe("…");
  });
  it("estimates read as estimates", () => {
    expect(jobTypeLabel({ is_estimate: true, type_id: "t1" }, types)).toBe("Estimate");
  });
});
