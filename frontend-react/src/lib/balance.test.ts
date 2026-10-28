import { describe, it, expect } from "vitest";
import { balanceView } from "./balance";

describe("balanceView", () => {
  it("positive balance means the client owes", () => {
    expect(balanceView(120)).toMatchObject({ label: "Owes", amount: 120, tone: "owes", className: "text-rose-600" });
  });
  it("negative balance means the client is in credit", () => {
    expect(balanceView(-45.5)).toMatchObject({ label: "In credit", amount: 45.5, tone: "credit", className: "text-emerald-600" });
  });
  it("zero, null and undefined are settled", () => {
    for (const v of [0, null, undefined, 0.001]) expect(balanceView(v)).toMatchObject({ label: "Settled", amount: 0, tone: "settled" });
  });
});
