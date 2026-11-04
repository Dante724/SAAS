import { describe, it, expect, beforeEach, vi } from "vitest";
import { askCancelReason, droppedStatus } from "./cancelReason";

const get = vi.fn();
const selectDialog = vi.fn();
vi.mock("../api/client", () => ({ api: { get: (...a: any[]) => get(...a) } }));
vi.mock("./dialog", () => ({ selectDialog: (...a: any[]) => selectDialog(...a) }));

beforeEach(() => {
  get.mockReset().mockResolvedValue({ items: [{ value: "Customer changed mind" }] });
  selectDialog.mockReset().mockResolvedValue("Customer changed mind");
});

describe("askCancelReason", () => {
  it("says dropped, not cancelled, when no status is named", async () => {
    await askCancelReason();
    expect(selectDialog.mock.calls[0][0]).toBe("Why is this job being dropped?");
    await askCancelReason(undefined, 3);
    expect(selectDialog.mock.calls[1][0]).toBe("Why are these 3 jobs being dropped?");
    expect(selectDialog.mock.calls.map((c) => c[0]).join(" ")).not.toMatch(/cancel/i);
  });

  it("names the status when there is one", async () => {
    expect(await askCancelReason("No repair")).toBe("Customer changed mind");
    expect(selectDialog.mock.calls[0][0]).toBe("Why is this job being marked “No repair”?");
    expect(selectDialog.mock.calls[0][1]).toEqual(["Customer changed mind"]);
  });

  it("finds a dropped-group status", () => {
    expect(droppedStatus([{ key: "a", status_group: "dropped" }, { key: "b", status_group: "done" }], "a")?.key).toBe("a");
    expect(droppedStatus([{ key: "b", status_group: "done" }], "b")).toBeUndefined();
  });
});
