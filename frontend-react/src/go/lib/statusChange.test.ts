import { describe, expect, it, vi } from "vitest";
import { changeStatus, refusal } from "./statusChange";
import { inferRole } from "./role";

describe("changeStatus", () => {
  const base = { orderId: "o1", from: "in_repair", to: "ready", toName: "Ready" };

  it("shows the new status at once and keeps it when the server agrees", async () => {
    const apply = vi.fn();
    const mutate = vi.fn().mockResolvedValue({ ok: true, queued: false, data: { status: "ready" } });
    const r = await changeStatus({ ...base, apply, mutate });
    expect(apply.mock.calls).toEqual([["ready"]]);
    expect(mutate).toHaveBeenCalledWith("PATCH", "/api/orders/o1/status", { status: "ready" });
    expect(r).toEqual({ kind: "saved", data: { status: "ready" } });
  });

  it("rolls back and explains a 409 gate", async () => {
    const apply = vi.fn();
    const mutate = vi.fn().mockResolvedValue({ ok: false, queued: false, status: 409, data: { detail: "'Ready' requires a comment on the order first." } });
    const r = await changeStatus({ ...base, apply, mutate });
    expect(apply.mock.calls).toEqual([["ready"], ["in_repair"]]);
    expect(r).toEqual({ kind: "refused", title: "Can't move to Ready yet", message: "'Ready' requires a comment on the order first." });
  });

  it("keeps the new status when the change is queued (offline or off the shop network)", async () => {
    const apply = vi.fn();
    const r1 = await changeStatus({ ...base, apply, mutate: vi.fn().mockResolvedValue({ ok: true, queued: true }) });
    expect(r1).toEqual({ kind: "queued", held: false });
    const r2 = await changeStatus({ ...base, apply, mutate: vi.fn().mockResolvedValue({ ok: true, queued: true, held: true }) });
    expect(r2).toEqual({ kind: "queued", held: true });
    expect(apply.mock.calls.every((c) => c[0] === "ready")).toBe(true);
  });

  it("rolls back if the call itself throws", async () => {
    const apply = vi.fn();
    const r = await changeStatus({ ...base, apply, mutate: vi.fn().mockRejectedValue(new Error("boom")) });
    expect(apply.mock.calls).toEqual([["ready"], ["in_repair"]]);
    expect(r.kind).toBe("refused");
  });
});

describe("refusal", () => {
  it("words the common refusals", () => {
    expect(refusal(403, { detail: "You can't move this order from 'new' to 'done'." }, "Done").message).toContain("can't move");
    expect(refusal(403, null, "Done").title).toBe("Not allowed");
    expect(refusal(409, null, "Done").message).toMatch(/needs finishing/);
    expect(refusal(401, null, "Done").title).toBe("Signed out");
  });
});

describe("inferRole", () => {
  it("picks a Today for the person", () => {
    expect(inferRole({ is_owner: true, role: "Technician" })).toBe("owner");
    expect(inferRole({ role: "Senior Tech" })).toBe("tech");
    expect(inferRole({ role: "Staff", home_route: "/my-work" })).toBe("tech");
    expect(inferRole({ role: "Receptionist" })).toBe("desk");
    expect(inferRole({ role: "admin" })).toBe("owner");
    expect(inferRole(null)).toBe("tech");
  });
});
